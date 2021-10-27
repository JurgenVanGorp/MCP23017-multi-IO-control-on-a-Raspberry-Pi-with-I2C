#!/usr/bin/env python
"""
MCP23017 Control Service. 
"""
import traceback
import linecache
import os
import time
import socket
import selectors
import types
import logging
from logging.handlers import RotatingFileHandler
import xml.etree.ElementTree as ET
from datetime import date, datetime
from smbus2 import SMBus
from threading import Thread, Lock

### USER DEFINED CONSTANTS ##############################################################
# CONFIGURATION_FILE is the name of the configuration file that will be written on the home
# location of the current user when running the program. The configuration file is read at
# the first start of the program, e.g. after a power failure, to make sure that the 
# MCP23017 devices are reconfigured to their latest state.
# The CONFIGURATION _FILE contains the latest configured MCP23017 DIR values, which will 
# be written on the I2C channel once on the bus after e.g. a cold boot.
# Remark that the dot in front of the filename makes it invisible for a regular ls.
# CONFIGURATION_FILE = ".mcp23017control.xml" --> Default value
# CONFIGURATION_FILE = ''  --> Set to empty string to disable this feature.
CONFIGURATION_FILE = ".mcp23017server.xml"
### You can change these constants to your own flavour
# TCP_IP is the port that the server is responding to.
# E.g. TCP_IP = 127.0.0.1 --> if this application is only used locally.
# E.g. TCP_IP = 192.168.1.200 --> or another .xxx bit at the end if this is your local network
# E.g. TCP_IP = '' --> (empty string) means that all active IPv4 ports are used. Also useable for DHCP
TCP_IP = ''
# TCP_PORT is the port that this program will listen to
# Please mind that numbers below 1024 are typically used by the system, so reach higher
TCP_PORT = 8888
# BUFFER_SIZE is maximum buffer that will be read. Default is 1024
BUFFER_SIZE = 1024       # Network connector buffer size
# If ports are already opened, the software will wait for the port to be opened every 
# second. It will retry this CONNECTION_ATTEMPTS times every RETRY_DELAY seconds.
CONNECTION_ATTEMPTS = 10 # If port is blocked, number of retries to claim port
RETRY_DELAY = 6          # Number of seconds to wait before retrying connection
# Give status reports, i.e. run the software in high communication mode?
# Verbose = 0 --> run quiet
# Verbose = 1 --> give details on user actions and errors only
# Verbose = 2 --> Babble, babble, babble ...
VERBOSE = 0
# DEMO_MODE_ONLY = True --> Print on screen what would happen on the I2C bus. Use this
#       when e.g. running the program manually (not as a service) to verify operation for
#       your own software.
# DEMO_MODE_ONLY = False --> Actually write the values on the I2C bus
DEMO_MODE_ONLY = False
# Acceptable Commands for controlling the I2C bus
# These are the commands you need to use to control the DIR register of the MCP23017, or
# for setting and clearing pins.
GETDIRBIT = "GETDBIT"         # Read the specific IO pin dir value (1 = output)
GETDIRREGISTER = "GETDIRREG"  # Read the full DIR register (low:1 or high:2)
SETDIRBIT = "SETDBIT"         # Set DIR pin command
CLEARDIRBIT = "CLRDBIT"       # Clear DIR pin command
GETIOPIN = "GETPIN"           # Read the specific IO pin value
GETIOREGISTER = "GETIOREG"    # Read the full IO register (low:1 or high:2)
SETDATAPIN = "SETPIN"         # Set pin to High
CLEARDATAPIN = "CLRPIN"       # Set pin to low
TOGGLEPIN = "TOGGLE"          # Toggle a pin to the "other" value for TOGGLEDELAY time
                              # If a pin is high, it will be set to low, and vice versa
TOGGLEDELAY = 0.1             # Seconds that the pin will be toggled. Default = 100 msec
# LOG_LEVEL determines the level of logging output into the system logs.
# Log Level = 0 --> No logging at all
# Log Level = 1 --> give details on application status and errors only
# Log Level = 2 --> Babble, babble, babble ...
# Remark that the dot in front of the filename makes it invisible. the file is saved 
# in your home folder.
LOG_LEVEL = 1
LOG_FILE = '.mcp23017server.log'
# Number of seconds to wait before writing an "Alive and Kicking" message in the log
# Set value to zero to disable this feature.
# Suggested debugging value is 60 (every minute)
# Default value is 0, in order to limit the number of writes on an SSD card.
AM_ALIVE_TIMER = 0

### PROGRAM CONSTANTS ####################################################################
# Software version
VERSION = '0.9.0'
# MCP23017 default parameters are that you can address the devices in the 0x20 to 0x2F 
# address space with the three selector pins. You can change these if you want to use 
# the software for other I2C devices.
MINBOARDID = 0x20        # Minimum I2C address 
MAXBOARDID = 0x2f        # Maximum I2C address
MINPIN = 0x00            # Minimum pin on the MCP23017
MAXPIN = 0x10            # Maximum pin on the MCP23017, +1 (i.e. must be lower than this value)
# TimeOut in seonds before the threads are considered dead. If the time-out is reached, 
# the thread will crash and die, and is expected to be restarted as a service
WATCHDOG_TIMEOUT = 5
### Define MCP23017 specific registers
IODIRA = 0x00    # IO direction A - 1= input 0 = output
IODIRB = 0x01    # IO direction B - 1= input 0 = output    
IPOLA = 0x02     # Input polarity A
IPOLB = 0x03     # Input polarity B
GPINTENA = 0x04  # Interrupt-onchange A
GPINTENB = 0x05  # Interrupt-onchange B
DEFVALA = 0x06   # Default value for port A
DEFVALB = 0x07   # Default value for port B
INTCONA = 0x08   # Interrupt control register for port A
INTCONB = 0x09   # Interrupt control register for port B
IOCON = 0x0A     # Configuration register
GPPUA = 0x0C     # Pull-up resistors for port A
GPPUB = 0x0D     # Pull-up resistors for port B
INTFA = 0x0E     # Interrupt condition for port A
INTFB = 0x0F     # Interrupt condition for port B
INTCAPA = 0x10   # Interrupt capture for port A
INTCAPB = 0x11   # Interrupt capture for port B
GPIOA = 0x12     # Data port A
GPIOB = 0x13     # Data port B
OLATA = 0x14     # Output latches A
OLATB = 0x15     # Output latches B
ALLOUTPUTS = "0xff" # Initial value of DIR register if not yet used
### END OF CONSTANTS SECTION #########################################################

class mcp23017broker():
    """
    A class that is a man in the middle between network communications and I2C attached devices.
    """
    def __init__(self, the_log, i2chandler, xmldata = None):
        # Copy logfile to local
        self._log = the_log
        # Mutex to make sure nobody else is messing with the I2C bus
        self._i2chandler = i2chandler
        # Inherit the xmldata communication
        self._xmldata = xmldata
        # selector for capturing events
        self._log.info(2, "Setting up a Selector.")
        self._sel = selectors.DefaultSelector()
        # Program watchdog and error flags (used for crashing the project)
        self._log.info(2, "Initializing Watchdog.")
        self._watchdog_thread1 = datetime.now()
        self._error_state = ""

    @property
    def error_state(self):
        return self._error_state
    
    @property
    def watchdog_CommandReceiver(self):
        return self._watchdog_thread1
    
    def SetupThreads(self):
        # Create parallel thread for listening to the network on port TCP_PORT
        self.thread1 = Thread(target = self.CommandReceiver, args = [TCP_PORT, BUFFER_SIZE], daemon = True)
        self._log.info(2, "Starting Command Receiver thread")
        self.thread1.start()

    def CommandReceiver(self, whichport = TCP_PORT, buffersize = BUFFER_SIZE):
        """
        Receives commands given over the network, does initial verification and executes command.
        """
        # Network communication socket
        self._lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        binding_attempts = 0
        bind_successful = False
        # Try CONNECTION_ATTEMPTS of times to listen on port TCP_PORT
        while binding_attempts < CONNECTION_ATTEMPTS:
            self._watchdog_thread1 = datetime.now()
            try:
                self._log.info(2, "Binding port {} to TCP address {}. Tried {} time(s). ".format(TCP_PORT, TCP_IP, binding_attempts))
                self._lsock.bind((TCP_IP, TCP_PORT))
                self._lsock.listen()
                binding_attempts = CONNECTION_ATTEMPTS + 1
                bind_successful = True
            except:
                # If connection unsuccessful, try again after one second.
                self._log.error(2, "Could not bind port {} to TCP address {}. Tried {} time(s). ".format(TCP_PORT, TCP_IP, binding_attempts))
                if VERBOSE == 2:
                    print(traceback.format_exc())
                time.sleep(RETRY_DELAY)
                binding_attempts += 1

        # Stop thread if binding was unsuccessful
        if not(bind_successful):
            self._log.error(1, "Could not bind TCP/IP port {}. I tried {} times. Giving up. ".format(TCP_PORT, CONNECTION_ATTEMPTS))
            self._error_state = "Could not bind TCP/IP port {}. I tried {} times. Giving up. ".format(TCP_PORT, CONNECTION_ATTEMPTS)
        else:
            if VERBOSE == 2:
                print("Listening on: ({}, {}).".format(whichport, buffersize))
                self._log.info(2, "Listening on: ({}, {}). ".format(whichport, buffersize))
            # Continue operations while listening on the port. This is done to allow parallel connections.
            self._lsock.setblocking(False)
            # Enable reading from the network socket
            self._sel.register(self._lsock, selectors.EVENT_READ, data=None)
            try:
                while True:
                    self._watchdog_thread1 = datetime.now()
                    # Continuously process data that is coming in on the network socket.
                    # the timeout is set to keep polling the watchdog timer
                    events = self._sel.select(timeout=0.5)
                    for key, mask in events:
                        if key.data is None:
                            # Create new data socket
                            self.accept_connection(key.fileobj)
                        else:
                            # Process data
                            self.service_connection(key, mask)
            except Exception as err:
                # Collect error data
                error_string = traceback.format_exc()
                self._log.error(1, "Error condition met: {}".format(error_string))
                if VERBOSE == 1:
                    print(error_string)
                if self._xmldata is not None:
                    self._xmldata.DeleteKey(board_id)
                self._error_state += "When processing network socket: {}. ".format(error_string)
            finally:
                self._sel.close()

    def accept_connection(self, sock):
        """
        Accepts a new connection from a new network client
        """
        # Get the client information
        connion, addrss = sock.accept()
        if VERBOSE == 2:
            print("Accepted incoming connection from: {}".format(addrss))
        self._log.info(2, "Accepted incoming connection from: {}".format(addrss))
        # Incoming connections are fall-through, i.e. don't block other connections when processing data
        connion.setblocking(False)
        # Set up a new selector that will catch all events coming from the client
        events = selectors.EVENT_READ | selectors.EVENT_WRITE
        # Get the connection data information
        indata = types.SimpleNamespace(addr=addrss, inb=b"", outb=b"")
        # Register the selector, and connect it to the incoming events
        self._log.info(2, "Registering the connection. ")
        self._sel.register(connion, events, data=indata)

    def service_connection(self, key, mask):
        """
        Process incoming data coming from the connected clients (one at the time).
        Properly formatted commands are processed immediately.
        """
        # Get socket info and data from the client
        sock = key.fileobj
        data = key.data
        # If receive buffer was hit
        if mask & selectors.EVENT_READ:
            # Pull the data from the socket
            recv_data = sock.recv(BUFFER_SIZE)
            if recv_data:
                # Add data to the output buffer.
                data.outb += recv_data
            else:
                # If data was empty, the client has closed the connection. So, close here also.
                if VERBOSE == 2:
                    print("Closing connection to: {}".format(data.addr))
                self._log.info(2, "Closing connection to: {}".format(data.addr))
                self._sel.unregister(sock)
                sock.close()

        # If send buffer was hit, i.e. when data is complete in the buffer
        if mask & selectors.EVENT_WRITE:
            # Verify if there is truly data in the buffer
            if data.outb:
                # Data is expected to be in the format: Command [space] Board_ID [space] Pin-Number or dummy value
                # Split the command on the spaces
                command_list = data.outb.split()
                self._log.info(2, "Processing command: {}".format(command_list))

                # Start the reply error with an empty error
                self._return_error = ""

                # First verify if truly three items given, otherwise it's an error already
                if len(command_list) != 3:
                    self._return_error += "Error: commands must have 3 fields: (command, board, data). "
                    self._log.info(2, "Error: commands must have 3 fields: (command, board, data).")
                else:
                    the_command = command_list[0].decode('utf-8')
                    the_board = command_list[1].decode('utf-8')
                    the_value = command_list[2].decode('utf-8')
                    # Using a try here, because the command could also be very dirty.
                    set_expectation = "Error: first command must be one of the following {}, {}, {}, {}, {}, {}, {}, {}, {}. ".format(GETDIRBIT, GETDIRREGISTER, SETDIRBIT, CLEARDIRBIT, GETIOPIN, GETIOREGISTER, SETDATAPIN, CLEARDATAPIN, TOGGLEPIN)
                    try:
                        if the_command not in {GETIOPIN, SETDIRBIT, CLEARDIRBIT, GETDIRBIT, SETDATAPIN, CLEARDATAPIN, GETIOREGISTER, GETDIRREGISTER, TOGGLEPIN}:
                            self._return_error += set_expectation
                            self._log.info(2, set_expectation)
                    except:
                        # Exception can happen if the_command is something _very_ weird, so need to capture that too without crashing
                        if VERBOSE == 2:
                            print(traceback.format_exc())
                        self._return_error += set_expectation
                        self._log.info(2, set_expectation)

                    # Test if Board ID is a hex number within allowed Board IDs
                    try:
                        test_value = int(the_board, 16)
                        if not(test_value in range(MINBOARDID, MAXBOARDID)):
                            self._return_error += "Error: Board ID not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINBOARDID, 2, MAXBOARDID-1, 2)
                            self._log.info(2, "Error: Board ID not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINBOARDID, 2, MAXBOARDID-1, 2))
                    except:
                        if VERBOSE == 2:
                            print(traceback.format_exc())
                        self._return_error += "Error: wrongly formatted register. "
                        self._log.info(2, "Error: wrongly formatted register. ")

                    # Test if the pin number is a hex number from 0x00 to 0x0f (included)
                    try:
                        test_value = int(the_value, 16)
                        if not(test_value in range(MINPIN, MAXPIN)):
                            self._return_error += "Error: registervalue not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINPIN, 2, MAXPIN, 2)
                            self._log.info(2, "Error: registervalue not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINPIN, 2, MAXPIN, 2))
                    except:
                        if VERBOSE == 2:
                            print(traceback.format_exc())
                        self._return_error += "Error: wrongly formatted data byte. "
                        self._log.info(2, "Error: wrongly formatted data byte. ")

                if self._return_error == '':
                    if VERBOSE == 2:
                        print("Processing: {}, {}, {}.".format(the_command, the_board, the_value))
                    # Command format looks good, now process it and get the result back
                    return_data = self.ProcessCommand([the_command, the_board, the_value])
                    # Send an "OK" if no error
                    sock.send(("{} OK\n".format(return_data)).strip().encode('utf-8'))
                    self._log.debug(2, "Action result: {} OK\n".format(return_data))
                else:
                    if VERBOSE == 1:
                        print(self._return_error)
                    # Send back an error on the Socket if the command was not properly formatted. Do nothing else
                    sock.send(("{}\n".format(self._return_error)).encode('utf-8'))
                
                # Reset the outbound buffer after processing the command.
                data.outb = ''.encode('utf-8')

    def ProcessCommand(self, command_data):
        """
        Identifies command and processes the command on the I2C bus.
        """
        # Break command into pieces
        task = command_data[0]
        board_id = command_data[1]
        pin = command_data[2]
        if VERBOSE == 1:
            print("Processing command [{}] on board [{}] for pin [{}]".format(task, board_id,pin))
        self._log.info(2, "Processing command [{}] on board [{}] for pin [{}]".format(task, board_id,pin))
        # Process I2C bus commands based
        return_byte = ""
        try:
            if task == GETDIRBIT:
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CDirPin(board_id, pin),2)
                self._log.info(2, "Received byte [{}] from pin [{}] on board [{}] through GetI2CDirPin".format(return_byte, board_id, pin))
            elif task == GETDIRREGISTER:
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CDirRegister(board_id, pin),2)
                self._log.info(2, "Received byte [{}] from pin [{}] on board [{}] through GetI2CDirRegister".format(return_byte, board_id, pin))
            elif task == SETDIRBIT:
                return_byte = ""
                self._i2chandler.SetI2CDirPin(board_id, pin)
                self._log.info(2, "Setting DIR bit [{}] on board [{}] through SetI2CDirPin".format(board_id, pin))
                if self._xmldata is not None:
                    self._xmldata.set_board_pin(board_id, pin)
            elif task == CLEARDIRBIT:
                return_byte = ""
                self._i2chandler.ClearI2CDirPin(board_id, pin)
                self._log.info(2, "Clearing DIR bit [{}] on board [{}] through ClearI2CDirPin".format(board_id, pin))
                if self._xmldata is not None:
                    self._xmldata.clear_board_pin(board_id, pin)
            elif task == GETIOPIN:
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CPin(board_id, pin),2)
                self._log.info(2, "Received byte [{}] from pin [{}] on board [{}] through GetI2CPin".format(return_byte, board_id, pin))
            elif task == GETIOREGISTER:
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CIORegister(board_id, pin),2)
                self._log.info(2, "Received Register [{}] from pin [{}] on board [{}] through GetI2CIORegister".format(return_byte, board_id, pin))
            elif task == SETDATAPIN:
                return_byte = ""
                self._i2chandler.SetI2CPin(board_id, pin)
                self._log.info(2, "Setting bit [{}] on board [{}] through SetI2CPin".format(board_id, pin))
            elif task == CLEARDATAPIN:
                return_byte = ""
                self._i2chandler.ClearI2CPin(board_id, pin)
                self._log.info(2, "Clearing bit [{}] on board [{}] through ClearI2CPin".format(board_id, pin))
            elif task == TOGGLEPIN:
                return_byte = ""
                self._i2chandler.ToggleI2CPin(board_id, pin)
                self._log.info(2, "Toggling bit [{}] on board [{}] through ToggleI2CPin".format(board_id, pin))
            else:
                if VERBOSE > 1:
                    print("Error: Did not understand command [{}].".format(task))
                self._log.info(2, "Error: Did not understand command [{}].".format(task))

        except Exception as err:
            error_string = traceback.format_exc()
            if VERBOSE == 1:
                print(error_string)
            if self._xmldata is not None:
                self._xmldata.DeleteKey(board_id)
            self._error_state += "Error when processing I2C command: {}. ".format(error_string)
            self._log.error(1, "Error when processing I2C command: {}. ".format(error_string))
        return return_byte

def InitBusAtBoot(the_log, xmldata, i2chandler):
    """
    If the program starts first time, pull the remembered boards from the config file. Set the proper input/output pin states to the last ones remembered.
    """
    # Mutex to make sure nobody else is messing with the I2C bus
    #####self.i2chandler = i2cCommunication()

    # Read the configured boards from the config file
    the_log.info(2, "Reading board information from XML parameter file.")
    boarddata = xmldata.get_all_boards
    # Process boards one by one
    for board in boarddata:
        # Get the board ID (hex board number)
        board_id = board.attrib["name"]
        # Process both ports in the MCP23017 board (if configured both)
        for port in board:
            # Get Port A or B ID
            port_id = port.attrib["name"]
            if VERBOSE == 2:
                print("Port [{}] of board [{}] should be set to [{}]".format(port_id, board_id, port.text))
            the_log.info(2, "Port [{}] of board [{}] should be set to [{}]".format(port_id, board_id, port.text))
            # Write the I/O state to the port
            if not(i2chandler.WriteI2CDir(board_id, port_id, port.text)):
                if VERBOSE == 2:
                    print("That didn't work for board [{}]".format(board_id))
                    the_log.info(2, "That didn't work for board [{}]".format(board_id))
                # If that didn't work, the board may have been removed before booting. Remove it from the config file.
                xmldata.DeleteKey(board_id)

class i2cCommunication():
    """
    A class for doing communications to MCP23017 devices on the Raspberry Pi I2C bus.
    """
    def __init__(self, the_log):
        # Copy logfile to local
        self._log = the_log
        # Create a new I2C bus (port 1 of the Raspberry Pi)
        if DEMO_MODE_ONLY:
            self.i2cbus = 0
        else:
            self.i2cbus = SMBus(1)
            self._log.info(2, "Initializing SMBus 1 (I2C).")
        # Set up a Mutual Exclusive lock, such that parallel threads are not interfering with another thread writing on the I2C bus
        self.i2cMutex = Lock()
        self._log.info(2, "Initialized I2C Mutex.")
        # Initialize the boards that are being handled.
        self.managedboards = []

    @property
    def allmanagedboards(self):
        return self.managedboards

    def CheckInitializeBoard(self, board_id):
        """
        Verifies if a board is already in the managed list.
        If not, the Control Register for the board is initialized.
        """
        # if board_id is given as a hex string, convert to int
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        return_value = True

        try:
            # check if a board is already managed. This lookup will result in an error if not
            dummy = (self.managedboards.index(board_id) >= 0)
        except:
            # Wait for the I2C bus to become free
            self._log.info(2, "Writing data [0x02] to IOCON register for board [0x{:0{}X}]".format(board_id, 2))
            self.i2cMutex.acquire()
            try:
                # Initialize configuration register of the new board
                if DEMO_MODE_ONLY:
                    print("SIMULATION : writing data [0x02] to IOCON register for board [0x{:0{}X}]".format(board_id, 2))
                else:
                    self.i2cbus.write_byte_data(board_id, IOCON, 0x02)
                # Since existing yet, add board to managed list if initialization was successful
                self.managedboards.append(board_id)
            except:
                # An error happened when accessing the new board, maybe non-existing on the bus
                return_value = False
            finally:
                # Free Mutex to avoid a deadlock situation
                self.i2cMutex.release()
        if not(return_value):
            self._log.error(2, "Writing [0x02] to IOCON register for board [0x{:0{}X}] Failed !".format(board_id, 2))
        return return_value

    def GetI2CDirPin(self, board_id, pin_nr):
        """
        Gets the current value of the DIR value of an pin on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(pin_nr,str)):
            pin_nr = int(pin_nr, 16)

        # Verify if MCP23017 pin number between 0 and 15
        if (pin_nr < 0) or (pin_nr > 15):
            return_value = -1
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = 1

                # Pin values up to 0x0f go to GPIOA, higher values go to GPIOB
                if (pin_nr > 7):
                    port_id = IODIRB
                    pin_nr = pin_nr % 8
                else:
                    port_id = IODIRA

                # Only start reading if the I2C bus is available
                self._log.info(2, "Reading DIR pin from port [0x{:0{}X}] of board [0x{:0{}X}]".format(port_id, 2, board_id, 2))
                self.i2cMutex.acquire()
                try:
                    if DEMO_MODE_ONLY:
                        return_value = (1 << pin_nr)
                        print("SIMULATION : reading DIR pin [0x{:0{}X}] from port [0x{:0{}X}] of board [0x{:0{}X}]".format(return_value, 2, port_id, 2, board_id, 2))
                    else:
                        # Read the current state of the IO register, then set ('OR') the one pin
                        if (self.i2cbus.read_byte_data(board_id, port_id) & (1 << pin_nr)) == 0x00:
                            return_value = 0
                        else:
                            return_value = 1
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = -1
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = -1
        return return_value
        
    def GetI2CDirRegister(self, board_id, reg_nr):
        """
        Gets the current value of the DIR value of an pin on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(reg_nr,str)):
            reg_nr = int(reg_nr, 16)

        # Verify if MCP23017 pin number between 0 and 15
        if (reg_nr < 0) or (reg_nr > 15):
            return_value = -1
            #aise Exception("Pin number must be between 0 and 15, but got [", pin_nr, "] for board ", board_id)
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = 1

                # Pin values up to 0x0f go to GPIOA, higher values go to GPIOB
                if (reg_nr > 0):
                    port_id = IODIRB
                else:
                    port_id = IODIRA

                # Only start reading if the I2C bus is available
                self._log.info(2, "Reading DIR register from port [0x{:0{}X}] of board [0x{:0{}X}]".format(port_id, 2, board_id, 2))
                self.i2cMutex.acquire()
                try:
                    if DEMO_MODE_ONLY:
                        return_value = 0xff
                        print("SIMULATION : reading DIR register [0x{:0{}X}] from port [0x{:0{}X}] of board [0x{:0{}X}]".format(return_value, 2, port_id, 2, board_id, 2))
                    else:
                        # Read the current state of the IO register, then set ('OR') the one pin
                        return_value = self.i2cbus.read_byte_data(board_id, port_id)
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = -1
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = -1
        return return_value
        
    def SetI2CDirPin(self, board_id, pin_nr):
        """
        Sets a pin to OUTPUT on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(pin_nr,str)):
            pin_nr = int(pin_nr, 16)
        # Verify if MCP23017 pin number between 0 and 15
        if (pin_nr < 0) or (pin_nr > 15):
            return_value = False
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = True

                # Pin values up to 0x0f go to IODIRA, higher values go to IODIRB
                if (pin_nr > 7):
                    port_id = IODIRB
                    pin_nr = pin_nr % 8
                else:
                    port_id = IODIRA

                # Only start writing if the I2C bus is available
                self._log.info(2, "Setting pin [0x{:0{}X}] to INPUT port [0x{:0{}X}] for board [0x{:0{}X}]".format(pin_nr, 2, port_id, 2, board_id,2))
                self.i2cMutex.acquire()
                try:
                    # Read the current state of the IODIR, then set ('OR') the one pin
                    if DEMO_MODE_ONLY:
                        data_byte = (1 << pin_nr)
                        print("SIMULATION : setting pin [0x{:0{}X}] to INPUT port [0x{:0{}X}] for board [0x{:0{}X}]".format(data_byte, 2, port_id, 2, board_id,2))
                    else:
                        data_byte = self.i2cbus.read_byte_data(board_id, port_id) | (1 << pin_nr)
                        self.i2cbus.write_byte_data(board_id, port_id, data_byte)
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = False
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = False
        return return_value
        
    def ClearI2CDirPin(self, board_id, pin_nr):
        """
        Sets a pin to INPUT on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(pin_nr,str)):
            pin_nr = int(pin_nr, 16)
        # Verify if MCP23017 pin number between 0 and 15
        if (pin_nr < 0) or (pin_nr > 15):
            return_value = False
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = True

                # Pin values up to 0x0f go to IODIRA, higher values go to IODIRB
                if (pin_nr > 7):
                    port_id = IODIRB
                    pin_nr = (pin_nr % 8)
                else:
                    port_id = IODIRA

                # Only start writing if the I2C bus is available
                self._log.info(2, "Setting pin [0x{:0{}X}] to OUPUT on port [0x{:0{}X}] for board [0x{:0{}X}]".format(pin_nr, 2, port_id, 2, board_id,2))
                self.i2cMutex.acquire()
                try:
                    if DEMO_MODE_ONLY:
                        data_byte = (1 << pin_nr)
                        print("SIMULATION : Setting pin [0x{:0{}X}] to OUTPUT on port [0x{:0{}X}] for board [0x{:0{}X}]".format(data_byte, 2, port_id, 2, board_id, 2))
                    else:
                        # Read the current state of the IODIR, then clear ('AND') the one pin
                        data_byte = self.i2cbus.read_byte_data(board_id, port_id) &  ~(1 << pin_nr)
                        self.i2cbus.write_byte_data(board_id, port_id, data_byte)
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = False
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = False
        return return_value

    def GetI2CPin(self, board_id, pin_nr):
        """
        Gets the current value of a pin on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(pin_nr,str)):
            pin_nr = int(pin_nr, 16)

        # Verify if MCP23017 pin number between 0 and 15
        if (pin_nr < 0) or (pin_nr > 15):
            return_value = -1
            #aise Exception("Pin number must be between 0 and 15, but got [", pin_nr, "] for board ", board_id)
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = 1

                # Pin values up to 0x0f go to GPIOA, higher values go to GPIOB
                if (pin_nr > 7):
                    port_id = GPIOB
                    pin_nr = pin_nr % 8
                else:
                    port_id = GPIOA

                # Only start reading if the I2C bus is available
                self._log.info(2, "Reading pin [0x{:0{}X}] from port [0x{:0{}X}] of board [0x{:0{}X}]".format(pin_nr, 2, port_id, 2, board_id, 2))
                self.i2cMutex.acquire()
                try:
                    if DEMO_MODE_ONLY:
                        return_value = (1 << pin_nr)
                        print("SIMULATION : reading pin [0x{:0{}X}] from port [0x{:0{}X}] of board [0x{:0{}X}]".format(return_value, 2, port_id, 2, board_id, 2))
                    else:
                        # Read the current state of the IO register, then set ('OR') the one pin
                        if (self.i2cbus.read_byte_data(board_id, port_id) & (1 << pin_nr)) == 0x00:
                            return_value = 0
                        else:
                            return_value = 1
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = -1
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = -1
        return return_value
        
    def GetI2CIORegister(self, board_id, reg_nr):
        """
        Gets the current value of a pin on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(reg_nr,str)):
            reg_nr = int(reg_nr, 16)

        # Verify if MCP23017 pin number between 0 and 15
        if (reg_nr < 0) or (reg_nr > 15):
            return_value = -1
            #aise Exception("Pin number must be between 0 and 15, but got [", pin_nr, "] for board ", board_id)
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = 1

                # Pin values up to 0x0f go to GPIOA, higher values go to GPIOB
                if (reg_nr > 0):
                    port_id = GPIOB
                else:
                    port_id = GPIOA

                # Only start reading if the I2C bus is available
                self._log.info(2, "Reading register [0x{:0{}X}], i.e. port [0x{:0{}X}] of board [0x{:0{}X}]".format(reg_nr, 2, port_id, 2, board_id, 2))
                self.i2cMutex.acquire()
                try:
                    if DEMO_MODE_ONLY:
                        return_value = 0xff
                        print("SIMULATION : reading register [0x{:0{}X}] from port [0x{:0{}X}] of board [0x{:0{}X}]".format(return_value, 2, port_id, 2, board_id, 2))
                    else:
                        # Read the current state of the IO register, then set ('OR') the one pin
                        return_value = self.i2cbus.read_byte_data(board_id, port_id)
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = -1
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = -1
        return return_value
        
    def SetI2CPin(self, board_id, pin_nr):
        """
        Sets a pin to HIGH on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(pin_nr,str)):
            pin_nr = int(pin_nr, 16)

        # Verify if MCP23017 pin number between 0 and 15
        if (pin_nr < 0) or (pin_nr > 15):
            return_value = False
            #aise Exception("Pin number must be between 0 and 15, but got [", pin_nr, "] for board ", board_id)
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = True

                # Pin values up to 0x0f go to GPIOA, higher values go to GPIOB
                if (pin_nr > 7):
                    port_id = GPIOB
                    pin_nr = pin_nr % 8
                else:
                    port_id = GPIOA

                # Only start writing if the I2C bus is available
                self._log.info(2, "Setting pin [0x{:0{}X}] to HIGH on port [0x{:0{}X}] for board [0x{:0{}X}]".format(pin_nr, 2, port_id, 2, board_id, 2))
                self.i2cMutex.acquire()
                try:
                    if DEMO_MODE_ONLY:
                        data_byte = (1 << pin_nr)
                        print("SIMULATION : setting pin [0x{:0{}X}] to HIGH on port [0x{:0{}X}] for board [0x{:0{}X}]".format(data_byte, 2, port_id, 2, board_id, 2))
                    else:
                        # Read the current state of the IO register, then set ('OR') the one pin
                        data_byte = self.i2cbus.read_byte_data(board_id, port_id) | (1 << pin_nr)
                        self.i2cbus.write_byte_data(board_id, port_id, data_byte)
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = False
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = False
        return return_value
        
    def ClearI2CPin(self, board_id, pin_nr):
        """
        Sets a pin to LOW on a board
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(pin_nr,str)):
            pin_nr = int(pin_nr, 16)

        # Verify if MCP23017 pin number between 0 and 15
        if (pin_nr < 0) or (pin_nr > 15):
            return_value = False
        else:
            # Verify if board used already, initialize if not
            if self.CheckInitializeBoard(board_id):
                return_value = True

                # Pin values up to 0x0f go to GPIOA, higher values go to GPIOB
                if (pin_nr > 7):
                    port_id = GPIOB
                    pin_nr = (pin_nr % 8)
                else:
                    port_id = GPIOA

                # Only start writing if the I2C bus is available
                self._log.info(2, "Setting pin [0x{:0{}X}] to LOW on port [0x{:0{}X}] for board [0x{:0{}X}]".format(pin_nr, 2, port_id, 2, board_id, 2))
                self.i2cMutex.acquire()
                try:
                    if DEMO_MODE_ONLY:
                        data_byte = (1 << pin_nr)
                        print("SIMULATION : setting pin [0x{:0{}X}] to LOW on port [0x{:0{}X}] for board [0x{:0{}X}]".format(data_byte, 2, port_id, 2, board_id, 2))
                    else:
                        # Read the current state of the IO register, then set ('OR') the one pin
                        data_byte = self.i2cbus.read_byte_data(board_id, port_id) &  ~(1 << pin_nr)
                        self.i2cbus.write_byte_data(board_id, port_id, data_byte)
                except:
                    # An error happened when accessing the new board, maybe non-existing on the bus
                    return_value = False
                finally:
                    # Free Mutex to avoid a deadlock situation
                    self.i2cMutex.release()
            else:
                return_value = False
        return return_value

    def ToggleI2CPin(self, board_id, pin_nr):
        """
        Toggles a bit on the board. If the pin is high, it will be momentarily set to low. If it is low, it will toggle to high.
        Pin number must be between 0 and 15
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(pin_nr,str)):
            pin_nr = int(pin_nr, 16)

        # Verify if MCP23017 pin number between 0 and 15
        if (pin_nr < 0) or (pin_nr > 15):
            return_value = False
        else:
            # Verify if board used already, initialize if not
            return_value = True
            if self.CheckInitializeBoard(board_id):
                self._log.info(2, "Toggling pin [0x{:0{}X}] on board [0x{:0{}X}]".format(pin_nr, 2, board_id, 2))
                current_state = self.GetI2CPin(board_id, pin_nr)
                if (current_state == 0x0) or (current_state == 0x1):
                    a_thread = Thread(target = self.PinToggler, args = [board_id, pin_nr, current_state], daemon = False)
                    a_thread.start()
                else:
                    return_value = False
            else:
                return_value = False
        return return_value
    
    def PinToggler(self, board_id, pin_nr, lowhigh_if_zero):
        if lowhigh_if_zero == 0x0:
            # Current state is low (0x0), and toggling needs to go to high briefly
            self.SetI2CPin(board_id, pin_nr)
            time.sleep(TOGGLEDELAY)
            self.ClearI2CPin(board_id, pin_nr)
        else: 
            # Current state is high (0x1 or more), and toggling needs to go to low briefly
            self.ClearI2CPin(board_id, pin_nr)
            time.sleep(TOGGLEDELAY)
            self.SetI2CPin(board_id, pin_nr)

    def ReadI2CDir(self, board_id, port_id):
        """
        Function for reading the full DIR Register value for a specific IO board
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(port_id,str)):
            port_id = int(port_id, 16)

        # Verify if board used already, initialize if not
        if self.CheckInitializeBoard(board_id):
            return_value = -1

            # Only start writing if the I2C bus is available
            self._log.info(2, "Reading DIR port [0x{:0{}X}] on board [0x{:0{}X}]".format(port_id, 2, board_id, 2))
            self.i2cMutex.acquire()
            try:
                # Read the current value of the DIR register
                if DEMO_MODE_ONLY:
                    print("SIMULATION : reading DIR port [0x{:0{}X}] on board [0x{:0{}X}]".format(port_id, 2, board_id, 2))
                    return_value = 0xff
                else:
                    return_value = self.i2cbus.read_byte_data(board_id, port_id)
            except:
                # An error happened when accessing the new board, maybe non-existing on the bus
                return_value = -1
            finally:
                # Free Mutex to avoid a deadlock situation
                self.i2cMutex.release()
        else:
            return_value = -1
        return return_value

    def WriteI2CDir(self, board_id, port_id, newvalue):
        """
        Function for writing the full DIR Register value for a specific IO board
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        if(isinstance(port_id,str)):
            port_id = int(port_id, 16)
        if(isinstance(newvalue,str)):
            newvalue = int(newvalue, 16)

        # Verify if board used already, initialize if not
        if self.CheckInitializeBoard(board_id):
            return_value = True

            # Only start writing if the I2C bus is available
            self._log.info(2, "Writing DIR port [0x{:0{}X}] on board [0x{:0{}X}] to new value [0x{:0{}X}]".format(port_id, 2, board_id, 2, newvalue, 2))
            self.i2cMutex.acquire()
            try:
                if DEMO_MODE_ONLY:
                    print("SIMULATION : writing DIR port [0x{:0{}X}] on board [0x{:0{}X}] to new value [0x{:0{}X}]".format(port_id, 2, board_id, 2, newvalue, 2))
                    return_value = True
                else:
                    # Write the new value of the DIR register
                    self.i2cbus.write_byte_data(board_id, port_id, newvalue)
                    # Verify if the value is indeed accepted
                    verification = self.i2cbus.read_byte_data(board_id, port_id)
                    if verification != newvalue:
                        return_value = False
            except:
                # An error happened when accessing the new board, maybe non-existing on the bus
                return_value = False
            finally:
                # Free Mutex to avoid a deadlock situation
                self.i2cMutex.release()
        else:
            return_value = False
        return return_value

    def BusIDBlinker(self, board_id = 0x20, num_flashes = 10):
        """
        Test routine only, briefly switches pin 15 on the board on and off. It is used to find back a board in the rack.
        Please mind that this is a specific routine which expects pin 15 of the MCP23017 to be set as output to an identification LED.
        """
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)
        for i in range(0, num_flashes):
            self.ClearI2CPin(board_id,15)
            time.sleep(0.5)
            self.SetI2CPin(board_id,15)
            time.sleep(0.5)

class xmlParameterHandler():
    """
    A class to handle an XML config file that keeps track of boards that were processed.
    """
    def __init__(self, the_log, xml_file_name = ''):
        # Copy logfile to local
        self._log = the_log
        # Only read config file if a name was provided
        if (CONFIGURATION_FILE == '') and (xml_file_name == ''):
            self._confdata = ET.fromstring(b'<DATA>\n <i2cboards>\n </i2cboards>\n</DATA>')
            self._use_config_file = False
        else:
            self._use_config_file = True
            from os.path import expanduser
            # Set location of file, go default if no file given
            if xml_file_name == "":
                self._filename = "{}/{}".format(expanduser("~"), CONFIGURATION_FILE)
            else:
                self._filename = xml_file_name
            # Create initial empty datastring
            self.read_parameter_file()

    @property
    def get_all_boards(self):
        return self._confdata[0]

    def get_board_dir(self, board_id, port_id):
        """
        Get the Direction value of a specific board
        """
        return_value = "0xff"
        if self._use_config_file:
            if(isinstance(board_id, int)):
                board_id = '0x{:0{}X}'.format(board_id,2)
            if(isinstance(port_id, int)):
                port_id = '0x{:0{}X}'.format(port_id,2)
            have_found_lev1 = False
            for child in self._confdata[0]:
                have_found_lev2 = False
                if child.attrib["name"] == board_id:
                    have_found_lev1 = True
                    for subchild in child:
                        if subchild.attrib["name"] == port_id:
                            return_value = subchild.text
                            have_found_lev2 = True
                    if (not(have_found_lev2)) or (len(child) != 2):
                        self._confdata[0].remove(child)
                        have_found_lev1 = False
            if not(have_found_lev1):
                self.CreateNewKey(board_id)
        return return_value

    def set_board_dir(self, board_id, port_id, newvalue):
        """
        Set the Direction value for a specific board
        """
        return_value = True
        if self._use_config_file:
            # if byte or integer given, update to hex byte
            if(isinstance(board_id, int)):
                board_id = '0x{:0{}X}'.format(board_id,2)
            if(isinstance(port_id, int)):
                port_id = '0x{:0{}X}'.format(port_id,2)
            if(isinstance(newvalue, int)):
                newvalue = '0x{:0{}X}'.format(newvalue,2)
            # Verify if value already exists (and create key if not in the file yet)
            comparevalue = self.get_board_dir(board_id, port_id)
            # update board and port pair, and write back to paramete file
            if comparevalue != newvalue:
                for child in self._confdata[0]:
                    if child.attrib["name"] == board_id:
                        for subchild in child:
                            if subchild.attrib["name"] == port_id:
                                subchild.text = newvalue
                return_value = self.write_parameter_file()
        return return_value

    def set_board_pin(self, board_id, pin_nr):
        """
        Set the pin value of the Direction register for a specific board
        """
        return_value = True
        if self._use_config_file:
            # Verify in inputs are given as hex. Convert to int if so
            if(isinstance(board_id,str)):
                board_id = int(board_id, 16)
            if(isinstance(pin_nr,str)):
                pin_nr = int(pin_nr, 16)
            # Pin values up to 0x0f go to IODIRA, higher values go to IODIRB
            if (pin_nr > 7):
                port_id = IODIRB
                pin_nr = pin_nr % 8
            else:
                port_id = IODIRA

            currentvalue = self.get_board_dir(board_id, port_id)
            if(isinstance(currentvalue,str)):
                currentvalue = int(currentvalue, 16)

            newvalue = currentvalue | (1 << pin_nr)
            return_value = self.set_board_dir(board_id, port_id, newvalue)
        return True

    def clear_board_pin(self, board_id, pin_nr):
        """
        Clear the pin value of the Direction register for a specific board
        """
        return_value = True
        if self._use_config_file:
            # Verify in inputs are given as hex. Convert to int if so
            if(isinstance(board_id,str)):
                board_id = int(board_id, 16)
            if(isinstance(pin_nr,str)):
                pin_nr = int(pin_nr, 16)
            # Pin values up to 0x0f go to IODIRA, higher values go to IODIRB
            if (pin_nr > 7):
                port_id = IODIRB
                pin_nr = pin_nr % 8
            else:
                port_id = IODIRA

            currentvalue = self.get_board_dir(board_id, port_id)
            if(isinstance(currentvalue,str)):
                currentvalue = int(currentvalue, 16)

            newvalue = currentvalue &  ~(1 << pin_nr)
            return_value = self.set_board_dir(board_id, port_id, newvalue)
        return return_value

    def DeleteKey(self, board_id):
        """
        Clear the Key in the XML file for a board that is apparently no longer used.
        """
        return_value = True
        if self._use_config_file:
            if(isinstance(board_id, int)):
                board_id = '0x{:0{}X}'.format(board_id,2)
            have_found = False
            for child in self._confdata[0]:
                if child.attrib["name"] == board_id:
                    have_found = True
                    self._confdata[0].remove(child)
            if have_found:
                return_value = self.write_parameter_file()
        return return_value

    def CreateNewKey(self, board_id):
        """
        Create a new Key in the XML file and set the initial values to OUTPUT (Oxff).
        """
        return_value = True
        if self._use_config_file:
            if(isinstance(board_id, int)):
                board_id = '0x{:0{}X}'.format(board_id,2)
            # make sure you are not creating a key that already exists
            self.DeleteKey(board_id)

            attrib = {'name': board_id}
            element = self._confdata[0].makeelement('board', attrib)
            self._confdata[0].append(element)
            index = len(self._confdata[0]) - 1

            attrib = {'name': '0x{:0{}X}'.format(IODIRA,2)}
            element = self._confdata[0][index].makeelement('port', attrib)
            element.text = ALLOUTPUTS
            self._confdata[0][index].append(element)

            attrib = {'name': '0x{:0{}X}'.format(IODIRB,2)}
            element = self._confdata[0][index].makeelement('port', attrib)
            element.text = ALLOUTPUTS
            self._confdata[0][index].append(element)

            return_value = self.write_parameter_file()
        return return_value

    def read_parameter_file(self):
        """
        Read the XML parameter file from the current home directory. Create an empty new one if nothing exists.
        """
        return_value = True
        if self._use_config_file:
            if os.path.exists(self._filename):
                self._log.info(2, "Reading Config XML file")
                try:
                    # Read file, this will fail if the file does not exist (yet)
                    ConfTree = ET.parse(self._filename)
                    self._confdata = ConfTree.getroot()
                except:
                    self._log.info(2, "Reading Config file FAILED. Creating a new one. ")
                    self._confdata = ET.fromstring(b'<DATA>\n <i2cboards>\n </i2cboards>\n</DATA>')
                    return_value = self.write_parameter_file()
            else:
                self._confdata = ET.fromstring(b'<DATA>\n <i2cboards>\n </i2cboards>\n</DATA>')
                return_value = self.write_parameter_file()
        return return_value

    def write_parameter_file(self):
        """
        Write the XML parameter file from the current home directory. Just try ...
        """
        return_value = True
        if self._use_config_file:
            self._log.info(2, "Writing Config file. ")
            try:
                xml_pretty_print(self._confdata[0])
                outString = ET.tostring(self._confdata)
                outFile = open(self._filename,"w")
                outFile.write(outString.decode('ascii'))
                outFile.close()
                return_value = True
            except Exception as err:
                return_value = False
                # Disable further write attempts if the file cannot be written.
                self._use_config_file = False
                if VERBOSE > 0:
                    print("Could not write parameter file [{}]. Error: {}".format(self._filename, err))
                self._log.info("Could not write parameter file [{}]. Error: {}".format(self._filename, err))
        return return_value

class LogThis():
    """
    A class for keeping track of the logging.
    """
    def __init__(self):
        # Set Logging details
        if LOG_LEVEL > 0:
            self._log_enabled = True
            try:
                from os.path import expanduser
                # Set location of file, go default if no file given
                self._filename = "{}/{}".format(expanduser("~"), LOG_FILE)
                self.log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
                self.my_handler = RotatingFileHandler(self._filename, mode='a', maxBytes=10*1024*1024, backupCount=2, encoding=None, delay=0)
                self.my_handler.setFormatter(self.log_formatter)
                self.my_handler.setLevel(logging.INFO)
                self.app_log = logging.getLogger('root')
                self.app_log.setLevel(logging.INFO)
                self.app_log.addHandler(self.my_handler)
            except Exception as err:
                self._log_enabled = False
                if VERBOSE > 0:
                    print("Error while creating log file: {}. ".format(str(err)))
        else:
            self._log_enabled = False
    
    def info(self, info_level, info_text):
        if self._log_enabled:
            if (LOG_LEVEL > 1) or (info_level == LOG_LEVEL):
                self.app_log.info(info_text)

    def debug(self, info_level, info_text):
        if self._log_enabled:
            if (LOG_LEVEL > 1) or (info_level == LOG_LEVEL):
                self.app_log.debug(info_text)

    def error(self, info_level, info_text):
        if self._log_enabled:
            if (LOG_LEVEL > 1) or (info_level == LOG_LEVEL):
                self.app_log.error(info_text)

def xml_pretty_print(element, level=0):
    """
    Format the XML data as properly indented items for better reading.
    """
    # Inspired by https://norwied.wordpress.com/2013/08/27/307/
    # Kudos go to Norbert and Chris G. Sellers
    padding = '  '
    indent = "\n{}".format(padding * level)
    if len(element):
        if not element.text or not element.text.strip():
            element.text = "{} ".format(indent)
        if not element.tail or not element.tail.strip():
            element.tail = indent
        for elem in element:
            xml_pretty_print(elem, level+1)
        if not element.tail or not element.tail.strip():
            element.tail = indent
    else:
        if level and (not element.tail or not element.tail.strip()):
            element.tail = indent

def main():
    '''
    Main program function
    '''
    # processcounter is used to determine when the program will send an info "alive" message
    process_counter = 0
    # Start a logger and provide info
    my_log = LogThis()
    my_log.info(1, "mcp23017control starting, running version [{}].".format(VERSION))
    # Parameter file for board input/output configurations
    my_log.info(2, "Creating XML Parameter Handler")
    xmldata = xmlParameterHandler(my_log)
    # Mutex to make sure nobody else is messing with the I2C bus
    my_log.info(2, "Creating I2C Communication Handler")
    i2chandler = i2cCommunication(my_log)
    # Initialize the I2C bus at first boot
    my_log.info(2, "Initializing I2C devices")
    InitBusAtBoot(my_log, xmldata, i2chandler)
    # Set up a new broker
    my_log.info(2, "Creating a Message Broker")
    mybroker = mcp23017broker(my_log, i2chandler, xmldata)
    # Create and start threads on the broker
    my_log.info(2, "Setting up Threads")
    mybroker.SetupThreads()
    # IF no errors in the threads, wait here forever
    my_error_state = ""
    while (my_error_state == ""):
        my_error_state = mybroker.error_state
        if (datetime.now() - mybroker.watchdog_CommandReceiver).total_seconds()  > (WATCHDOG_TIMEOUT * RETRY_DELAY):
            my_error_state += "CommandReceiver Thread stopped abnormally, unknown error. "
            my_log.error(1, "CommandReceiver watchdog timed out. ")
        else:
            process_counter += 1
            if (AM_ALIVE_TIMER > 0) and (process_counter > AM_ALIVE_TIMER):
                process_counter = 0
                my_log.info(2, "CommandReceiver thread alive and kicking")
                if VERBOSE == 2:
                    print("CommandReceiver thread alive and kicking. Last update: {} seconds ago.".format((datetime.now() - mybroker.watchdog_CommandReceiver).total_seconds()))

        time.sleep(1)
    my_log.error(1, "FATAL error: {}".format(my_error_state))
    raise Exception(my_error_state)

if __name__ == "__main__":
    """
    Entry point when program is called from the command line.
    """
    main()


