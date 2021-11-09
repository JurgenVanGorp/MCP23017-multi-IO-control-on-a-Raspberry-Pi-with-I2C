#!/usr/bin/env python
"""
MCP23017 Control Service. 
A service that acts as an interface between (e.g. Home Assistant) clients and the I2C bus on a Raspberry Pi.
Author: find me on codeproject.com --> JurgenVanGorp
"""
import traceback
import os
import sys
import time
import logging
import redis
from logging.handlers import RotatingFileHandler
import xml.etree.ElementTree as ET
from datetime import datetime
from smbus2 import SMBus
from threading import Thread, Lock

VERSION = "1.00"

###
### USER EDITABLE CONSTANTS #####################################################################
###
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

# LOG_LEVEL determines the level of logging output into the system logs.
# Log Level = 0 --> No logging at all
# Log Level = 1 --> (DEFAULT) give details on application status and errors only
# Log Level = 2 --> Babble, babble, babble ...
# Remark that the dot in front of the filename makes it invisible. the file is saved 
# in your home folder.
LOG_LEVEL = 1
LOG_FILE = '.mcp23017server.log'

# DEMO_MODE_ONLY = True --> Print on screen what would happen on the I2C bus. Use this
#       when e.g. running the program manually (not as a service) to verify operation for
#       your own software.
# DEMO_MODE_ONLY = False --> Actually write the values on the I2C bus
DEMO_MODE_ONLY = False

# Acceptable Commands for controlling the I2C bus
# These are the commands you need to use to control the DIR register of the MCP23017, or
# for setting and clearing pins.

FINDBOARD = "IDENTIFY"        # Identify Board number, return 1 if found on the I2C bus
GETDIRBIT = "GETDBIT"         # Read the specific IO pin dir value (1 = output)
GETDIRREGISTER = "GETDIRREG"  # Read the full DIR register (low:1 or high:2)
SETDIRBIT = "SETDBIT"         # Set DIR pin to INPUT (1)
CLEARDIRBIT = "CLRDBIT"       # Clear DIR pin command to OUTPUT (0)
GETIOPIN = "GETPIN"           # Read the specific IO pin value
GETIOREGISTER = "GETIOREG"    # Read the full IO register (low:1 or high:2)
SETDATAPIN = "SETPIN"         # Set pin to High
CLEARDATAPIN = "CLRPIN"       # Set pin to low
TOGGLEPIN = "TOGGLE"          # Toggle a pin to the "other" value for TOGGLEDELAY time
                              # If a pin is high, it will be set to low, and vice versa
TOGGLEDELAY = 0.1             # Seconds that the pin will be toggled. Default = 100 msec

# The COMMAND_TIMEOUT value is the maximum time (in seconds) that is allowed between pushing a  
# button and the action that must follow. This is done to protect you from delayed actions 
# whenever the I2C bus is heavily used, or the CPU is overloaded. If you e.g. push a button, 
# and the I2C is too busy with other commands, the push-button command is ignored when  
# COMMAND_TIMEOUT seconds have passed. Typically you would push the button again if nothing 
# happens after one or two seconds. If both commands are stored, the light is switched on and
# immediately switched off again.
# Recommended minimum value one or two seconds
# COMMAND_TIMEOUT = 2
# Recommended maximum value is 10 seconds. Feel free to set higher values, but be prepared that 
# you can can experience strange behaviour if there is a lot of latency on the bus.
COMMAND_TIMEOUT = 1.5

# Communications between Clients and the server happen through a Redis in-memory database
# so to limit the number of writes on the (SSD or microSD) storage. For larger implementations
# dozens to hundreds of requests can happen per second. Writing to disk would slow down the 
# process, and may damage the storage.
# Make sure to have Redis installed in the proper locations, e.g. also in the virtual python
# environments. The default is that Redis is installed on localhost (127.0.0.1).
REDIS_HOST = 'localhost'
REDIS_PORT = 6379

###
### PROGRAM INTERNAL CONSTANTS ####################################################################
###
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
IODIRA = 0x00            # IO direction A - 1= input 0 = output
IODIRB = 0x01            # IO direction B - 1= input 0 = output    
IPOLA = 0x02             # Input polarity A
IPOLB = 0x03             # Input polarity B
GPINTENA = 0x04          # Interrupt-onchange A
GPINTENB = 0x05          # Interrupt-onchange B
DEFVALA = 0x06           # Default value for port A
DEFVALB = 0x07           # Default value for port B
INTCONA = 0x08           # Interrupt control register for port A
INTCONB = 0x09           # Interrupt control register for port B
IOCON = 0x0A             # Configuration register
GPPUA = 0x0C             # Pull-up resistors for port A
GPPUB = 0x0D             # Pull-up resistors for port B
INTFA = 0x0E             # Interrupt condition for port A
INTFB = 0x0F             # Interrupt condition for port B
INTCAPA = 0x10           # Interrupt capture for port A
INTCAPB = 0x11           # Interrupt capture for port B
GPIOA = 0x12             # Data port A
GPIOB = 0x13             # Data port B
OLATA = 0x14             # Output latches A
OLATB = 0x15             # Output latches B
ALLOUTPUTS = "0xff"      # Initial value of DIR register if not yet used

# The dummy command is sent during initialization of the database and verification if
# the database can be written to. Dummy commands are not processed.
DUMMY_COMMAND = 'dummycommand'

### END OF CONSTANTS SECTION #########################################################

class databaseHandler():
    """
    A class for communicating between the server and clients through a shared memory Redis
    database. Two databases are initiated (or used) for communicating from client to 
    server (0) or from server to client (1).
    """
    def __init__(self, the_log):
        # Commands have id   datetime.now().strftime("%d-%b-%Y %H:%M:%S.%f")}, i.e. the primary key is a timestamp. 
        # Commands given at exactly the same time, will overwrite each other, but this is not expected to happen.
        # The commands table is then formatted as (all fields are TEXT, even if formatted as "0xff" !!)
        # id, command TEXT, boardnr TEXT DEFAULT '0x00', pinnr TEXT DEFAULT '0x00', datavalue TEXT DEFAULT '0x00'
        self._commands = None
        # Responses have id   datetime.now().strftime("%d-%b-%Y %H:%M:%S.%f")}, i.e. the primary key is a timestamp. 
        # The Responses table is then formatted as (all fields are TEXT, even if formatted as "0xff" !!)
        # id, command_id TEXT, datavalue TEXT, response TEXT
        self._responses = None
        # Copy logfile to local
        self._log = the_log
        # Initialize database
        self.OpenAndVerifyDatabase()

    def OpenAndVerifyDatabase(self):
        """
        Opens an existing database, or creates a new one if not yet existing. Then 
        verifies if the Redis database is accessible.
        """
        # First try to open the database itself.
        try:
            # Open the shared memory databases.
            # Redis database [0] is for commands that are sent from the clients to the server.
            nowTrying = "Commands"
            self._log.info(1, "Opening Commands database.")
            self._commands = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=0)
            # Redis database [1] is for responses from the server so the clients.
            nowTrying = "Responses"
            self._log.info(1, "Opening Responses database.")
            self._responses = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=1)
        except OSError as err:
            # Capturing OS error.
            self._log.error(1, "FATAL OS ERROR. Could not open [{}] database. This program is now exiting with error [{}].".format(nowTrying, err))
            # If a database cannot be opened, this program makes no sense, so exiting.
            sys.exit(1)
        except:
            # Capturing all other errors.
            self._log.error(1, "FATAL UNEXPECTED ERROR. Could not open [{}] database. This program is now exiting with error [{}].".format(nowTrying, sys.exc_info()[0]))
            # If a database cannot be opened, this program makes no sense, so exiting.
            sys.exit(1)
        
        # Do a dummy write to the Commands database, as verification that the database is fully up and running.
        try:
            # Remember: fields are: id, command TEXT, boardnr TEXT DEFAULT '0x00', pinnr TEXT DEFAULT '0x00', datavalue TEXT DEFAULT '0x00'
            self._log.info(2, "Verifying Commands database with dummy write.")
            id =  (datetime.now() - datetime.utcfromtimestamp(0)).total_seconds()
            datamap = {'command':DUMMY_COMMAND, 'boardnr':0x00, 'pinnr':0xff, 'datavalue':0x00}
            # Write the info to the Redis database
            self._commands.hset(id, None, None, datamap)
            # Set expiration to a short 1 second, after which Redis will automatically delete the record
            self._commands.expire(id, 1)
        except:
            # Capturing all errors.
            self._log.error(1, "FATAL UNEXPECTED ERROR. Could not read and/or write the [Commands] database. This program is now exiting with error [{}].".format(sys.exc_info()[0]))
            # If a database cannot be processed, this program makes no sense, so exiting.
            sys.exit(1)

        # Next, do a dummy write to the Responses database, as verification that the database is fully up and running.
        try:
            # Remember: fields are: id, command_id TEXT, datavalue TEXT, response TEXT
            self._log.info(2, "Verifying Responses database with dummy write.")
            id =  (datetime.now() - datetime.utcfromtimestamp(0)).total_seconds()
            datamap = {'datavalue':0x00, 'response':'OK'}
            # Write the info to the Redis database
            self._responses.hset(id, None, None, datamap)
            # Set expiration to a short 1 second, after which Redis will automatically delete the record
            self._responses.expire(id, 1)
        except:
            # Capturing all errors.
            self._log.error(1, "FATAL UNEXPECTED ERROR. Could not read and/or write the [Responses] database. This program is now exiting with error [{}].".format(sys.exc_info()[0]))
            # If a database cannot be processed, this program makes no sense, so exiting.
            sys.exit(1)

    def GetNextCommand(self):
        """
        Fetches the oldest command - that has not expired - from the commands buffer.
        """
        # Get all keys from the Commands table
        rkeys = self._commands.keys("*")
        # Key IDs are based on the timestamp, so sorting will pick the oldest first
        rkeys.sort()
        # Check if there are keys available
        if len(rkeys) > 0:
            # Get the first key from the list
            id = rkeys[0]
            # Read the Redis data
            datarecord = self._commands.hgetall(id)
            # We have the data, now delete the record (don't wait for the time-out)
            self._commands.delete(id)
            # pull the data from the record, and do proper conversions.
            # Correct potential dirty entries, to avoid that the software crashes on poor data.
            try:
                return_id = float(id.decode('ascii'))
            except:
                return_id = 0

            try:
                command =  datarecord[b'command'].decode('ascii')
            except:
                command = ''

            try:
                boardnr =  datarecord[b'boardnr'].decode('ascii')
            except:
                boardnr = 0x00

            try:
                pinnr =  datarecord[b'pinnr'].decode('ascii')
            except:
                pinnr = 0x00

            try:
                datavalue =  datarecord[b'datavalue'].decode('ascii')
            except:
                datavalue = 0x00
            # return the data read
            return(return_id, command, boardnr, pinnr, datavalue)
        else:
            # return a zero record if nothing was received
            return (0, '', 0x00, 0x00, 0x00)

    def ReturnResponse(self, id, value, response):
        """
        Returns the data value to the client through the Responses buffer. 
        Also does the house-keeping, deleting all old entries that would still exist.
        """
        # Remember: fields are : id, command_id TEXT, datavalue TEXT, response TEXT
        # The Response ID is the same as the Command ID, making it easy for the client to capture the data.
        mapping = {'command_id':id, 'datavalue':value, 'response':response}
        self._responses.hset(id, None, None, mapping)
        # set auto-delete time-out in the Redis database. Add several seconds grace period, and round to integer values
        self._responses.expire(id, round(COMMAND_TIMEOUT + 2))

class mcp23017broker():
    """
    A class that is a man in the middle between external clients and I2C attached devices.
    This class is based on a shared memory database.
    """
    def __init__(self, the_log, i2chandler, xmldata = None):
        # Copy logfile to local
        self._log = the_log
        # Create a handler for the I2C communications
        self._i2chandler = i2chandler
        # Inherit the xmldata communication
        self._xmldata = xmldata
        # Create a data pipe to the in-memory database
        self._datapipe = databaseHandler(self._log)

    def service_commands(self):
        """
        Process incoming data coming from the connected clients (one at the time).
        Properly formatted commands are processed immediately, or as separate threads (for long-lasting commands).
        """
        # Fetch a command from the pipe
        command_list = self._datapipe.GetNextCommand()
        # a command id larger than 0 is a successful read. Command ID zero is returned if the pipe is empty.
        if command_list[0] > 0:
            self._log.info(2, "Received command with id [{}]: [{}] for board [{}] and pin [{}].".format(str(command_list[0]), command_list[1], str(command_list[2]), str(command_list[3])))
            # Start the reply error with an empty error
            self._return_error = ""
            # retrieve commands from the pipe
            command_id = command_list[0]
            the_command = command_list[1]
            the_board = command_list[2]
            the_pin = command_list[3]
            # During initialization a dummy command is sent. This is also done by the clients, so make sure that these commands are thrown away.
            if the_command != DUMMY_COMMAND:
                # Inputs can have different formats, also numerical as hexadecimal (e.g. '0x0f'). Convert where necessary.
                if(isinstance(the_board,str)):
                    if 'x' in the_board:
                        the_board = int(the_board, 16)
                    else:
                        the_board = int(the_board, 10)
                the_value = command_list[3]
                if(isinstance(the_value,str)):
                    if 'x' in the_value:
                        the_value = int(the_value, 16)
                    else:
                        the_value = int(the_value, 10)
                # Describe what we are expecting on the bus.
                set_expectation = "Error: first command must be one of the following {}, {}, {}, {}, {}, {}, {}, {}, {}, {}. ".format(FINDBOARD, GETDIRBIT, GETDIRREGISTER, SETDIRBIT, CLEARDIRBIT, GETIOPIN, GETIOREGISTER, SETDATAPIN, CLEARDATAPIN, TOGGLEPIN)
                # Using a try here, because the command could also be very, very dirty.
                try:
                    if the_command not in {FINDBOARD, GETIOPIN, SETDIRBIT, CLEARDIRBIT, GETDIRBIT, SETDATAPIN, CLEARDATAPIN, GETIOREGISTER, GETDIRREGISTER, TOGGLEPIN}:
                        self._return_error += set_expectation
                        self._log.info(2, set_expectation)
                except:
                    # Exception can happen if the_command is something _very_ weird, so need to capture that too without crashing
                    self._return_error += set_expectation
                    self._log.info(2, set_expectation)
                
                # Test if Board ID is a hex number within allowed Board IDs
                try:
                    if not(the_board in range(MINBOARDID, MAXBOARDID)):
                        self._return_error += "Error: Board ID not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINBOARDID, 2, MAXBOARDID-1, 2)
                        self._log.info(2, "Error: Board ID not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINBOARDID, 2, MAXBOARDID-1, 2))
                except:
                    # print error message to the systemctl log file
                    if LOG_LEVEL == 2:
                        print(traceback.format_exc())
                    self._return_error += "Error: wrongly formatted register. "
                    self._log.info(2, "Error: wrongly formatted register. ")

                # Test if the pin number is a hex number from 0x00 to 0x0f (included)
                try:
                    if not(the_value in range(MINPIN, MAXPIN)):
                        self._return_error += "Error: registervalue not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINPIN, 2, MAXPIN, 2)
                        self._log.info(2, "Error: registervalue not in range [0x{:0{}X}, 0x{:0{}X}]. ".format(MINPIN, 2, MAXPIN, 2))
                except:
                    # print error message to the systemctl log file
                    if LOG_LEVEL == 2:
                        print(traceback.format_exc())
                    self._return_error += "Error: wrongly formatted data byte. "
                    self._log.info(2, "Error: wrongly formatted data byte. ")
                
                # All checks done, continue processing if no errors were found.
                if self._return_error == '':
                    # print status message to the systemctl log file
                    if LOG_LEVEL == 2:
                        print("Processing: {}, {}, {}.".format(the_command, the_board, the_value))
                    # Command format looks good, now process it and get the result back
                    return_data = self.ProcessCommand(the_command, the_board, the_value)
                    # Send an "OK" back, since we didn't find an error.
                    self._datapipe.ReturnResponse(command_id, return_data, 'OK')
                    self._log.debug(2, "Action result: {} OK\n".format(return_data))
                else:
                    # print error message to the systemctl log file
                    if LOG_LEVEL > 0:
                        print(self._return_error)
                    # Send back an error if the command was not properly formatted. Do nothing else
                    self._datapipe.ReturnResponse(command_id, '0x00', self._return_error)

    def ProcessCommand(self, task, board_id, pin):
        """
        Identifies command and processes the command on the I2C bus.
        """
        # Process I2C bus commands based on board ID and Pin nr
        return_byte = ""
        try:
            if task == GETDIRBIT:
                self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CDirPin(board_id, pin),2)
                self._log.info(2, "Received byte [{}] from pin [{}] on board [{}] through GetI2CDirPin".format(return_byte, pin, board_id))
            elif task == FINDBOARD:
                self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                return_byte = '0x{:0{}X}'.format(self._i2chandler.IdentifyBoard(board_id),2)
                self._log.info(2, "Received byte [{}] from board [{}] through IdentifyBoard".format(return_byte, board_id))
            elif task == GETDIRREGISTER:
                self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CDirRegister(board_id, pin),2)
                self._log.info(2, "Received byte [{}] from pin [{}] on board [{}] through GetI2CDirRegister".format(return_byte, pin, board_id))
            elif task == SETDIRBIT:
                return_byte = ""
                self._i2chandler.SetI2CDirPin(board_id, pin)
                self._log.info(2, "Setting DIR bit [{}] on board [{}] through SetI2CDirPin".format(pin, board_id))
                if self._xmldata is not None:
                    self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                    self._xmldata.set_board_pin(board_id, pin)
            elif task == CLEARDIRBIT:
                return_byte = ""
                self._i2chandler.ClearI2CDirPin(board_id, pin)
                self._log.info(2, "Clearing DIR bit [{}] on board [{}] through ClearI2CDirPin".format(pin, board_id))
                if self._xmldata is not None:
                    self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                    self._xmldata.clear_board_pin(board_id, pin)
            elif task == GETIOPIN:
                self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CPin(board_id, pin),2)
                self._log.info(2, "Received byte [{}] from pin [{}] on board [{}] through GetI2CPin".format(return_byte, pin, board_id))
            elif task == GETIOREGISTER:
                self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                return_byte = '0x{:0{}X}'.format(self._i2chandler.GetI2CIORegister(board_id, pin),2)
                self._log.info(2, "Received Register [{}] from pin [{}] on board [{}] through GetI2CIORegister".format(return_byte, pin, board_id))
            elif task == SETDATAPIN:
                return_byte = ""
                self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                self._i2chandler.SetI2CPin(board_id, pin)
                self._log.info(2, "Setting bit [{}] on board [{}] through SetI2CPin".format(pin, board_id))
            elif task == CLEARDATAPIN:
                return_byte = ""
                self._i2chandler.WaitForPinToBeReleased(board_id, pin, False)
                self._i2chandler.ClearI2CPin(board_id, pin)
                self._log.info(2, "Clearing bit [{}] on board [{}] through ClearI2CPin".format(pin, board_id))
            elif task == TOGGLEPIN:
                return_byte = ""
                self._i2chandler.ToggleI2CPin(board_id, pin)
                self._log.info(2, "Toggling bit [{}] on board [{}] through ToggleI2CPin".format(pin, board_id))
            else:
                # print error message to the systemctl log file
                if LOG_LEVEL > 1:
                    print("Error: Did not understand command [{}].".format(task))
                self._log.error(2, "Error: Did not understand command [{}].".format(task))

        except Exception as err:
            error_string = traceback.format_exc()
            # print error message to the systemctl log file
            if LOG_LEVEL == 1:
                print(error_string)
            if self._xmldata is not None:
                self._xmldata.DeleteKey(board_id)
            self._log.error(1, "Error when processing I2C command: {}.".format(error_string))
        return return_byte

class i2cCommunication():
    """
    A class for doing communications to MCP23017 devices on the Raspberry Pi I2C bus.
    """
    def __init__(self, the_log):
        # Copy logfile to local
        self._log = the_log
        self._log.info(2, "Initializing I2C Communication class.")
        # Create an empty set to be used for avoiding that multiple toggle commands can operate on the same pin
        # A mutex is needed to manage the self._toggle_set in a unique way
        self._toggle_set = set()
        self._toggle_mutex = Lock()
        # Create a new I2C bus (port 1 of the Raspberry Pi)
        if DEMO_MODE_ONLY:
            self.i2cbus = 0
        else:
            self.i2cbus = SMBus(1)
            self._log.info(2, "Initializing SMBus 1 (I2C).")
        # Set up a Mutual Exclusive lock, such that parallel threads are not interfering with another thread writing on the I2C bus
        self._i2cMutex = Lock()
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
            self._i2cMutex.acquire()
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
                self._i2cMutex.release()
        if not(return_value):
            self._log.error(2, "Writing [0x02] to IOCON register for board [0x{:0{}X}] Failed !".format(board_id, 2))
        return return_value

    def ReadI2CDir(self, board_id, port_id):
        """
        Function for reading the full DIR Register value for a specific IO board.
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
            self._i2cMutex.acquire()
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
                self._i2cMutex.release()
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
            self._i2cMutex.acquire()
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
                self._i2cMutex.release()
        else:
            return_value = False
        return return_value

    def IdentifyBoard(self, board_id):
        """
        Identifies if board exists on the I2C bus.
        """
        # Verify in inputs are given as hex. Convert to int if so
        if(isinstance(board_id,str)):
            board_id = int(board_id, 16)

        # Verify if board used already, initialize if not
        if self.CheckInitializeBoard(board_id):
            return_value = 1

            # Pin values up to 0x0f go to GPIOA, higher values go to GPIOB
            pin_nr = 1  # pick random pin number to be read from the board. We are not going to use it anyway.
            port_id = IODIRA

            # Only start reading if the I2C bus is available
            self._log.info(2, "Reading DIR pin from port [0x{:0{}X}] of board [0x{:0{}X}]".format(port_id, 2, board_id, 2))
            #self.i2cMutex.acquire()
            try:
                if DEMO_MODE_ONLY:
                    return_value = (1 << pin_nr)
                    print("SIMULATION : reading DIR pin [0x{:0{}X}] from port [0x{:0{}X}] of board [0x{:0{}X}]".format(return_value, 2, port_id, 2, board_id, 2))
                else:
                    # Read the current state of the IO register, then set ('OR') the one pin
                    _ = self.i2cbus.read_byte_data(board_id, port_id) & (1 << pin_nr)
                    return_value = 1
            except:
                # An error happened when accessing the new board, maybe non-existing on the bus
                return_value = 0
            #finally:
            #    # Free Mutex to avoid a deadlock situation
            #    self.i2cMutex.release()
        else:
            return_value = 0
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
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
            else:
                return_value = -1
        return return_value
        
    def GetI2CDirRegister(self, board_id, reg_nr):
        """
        Gets the current value of the DIR value of a pin on a board
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
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
            else:
                return_value = -1
        return return_value
        
    def SetI2CDirPin(self, board_id, pin_nr):
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
                    pin_nr = pin_nr % 8
                else:
                    port_id = IODIRA

                # Only start writing if the I2C bus is available
                self._log.info(2, "Setting pin [0x{:0{}X}] to INPUT port [0x{:0{}X}] for board [0x{:0{}X}]".format(pin_nr, 2, port_id, 2, board_id,2))
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
            else:
                return_value = False
        return return_value
        
    def ClearI2CDirPin(self, board_id, pin_nr):
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
                    pin_nr = (pin_nr % 8)
                else:
                    port_id = IODIRA

                # Only start writing if the I2C bus is available
                self._log.info(2, "Setting pin [0x{:0{}X}] to OUTPUT on port [0x{:0{}X}] for board [0x{:0{}X}]".format(pin_nr, 2, port_id, 2, board_id,2))
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
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
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
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
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
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
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
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
                self._i2cMutex.acquire()
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
                    self._i2cMutex.release()
            else:
                return_value = False
        return return_value

    def ToggleI2CPin(self, board_id, pin_nr, acquire_state = False):
        """
        Toggles a bit on the board. If the pin is high, it will be momentarily set to low. If it is low, it will toggle to high.
        Pin number must be between 0 and 15.
        Per default it is expected that the pin is low in the "off" state and has to be toggled high, e.g. to trigger a momentary
        switch. In some cases, the trigger is to the "other" side. acquire_state can be set to first assess the pin and briefly
        toggle the pin to the other high/low state.
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
            return_value = True
            # Toggling can take a long time, during which the server would not be able to process additional commands.
            # To avoid that the server is frozen, toggles are processed in separate threads.
            a_thread = Thread(target = self.PinToggler, args = [board_id, pin_nr], daemon = False)
            a_thread.start()
        return return_value
    
    def WaitForPinToBeReleased(self, board_id, pin_nr, lock_if_free = False):
        """
        Toggling can take a long time, during which the server would not be able to process additional commands.
        To avoid that the server is frozen, toggles are processed in separate threads. The boards being 
        processed are maintained in the _toggle_set. As long as a thread has a toggle action going on, no other
        actions are allowed on the specific board/pin combination. Therefore, all writes have to wait for the
        pin to be freed up again.
        """
        # The verification can not last longer than a TOGGLEDELAY. Keep track of the time, and time-out if necessary
        checking_time = datetime.now()
        keep_checking = True
        while keep_checking:
            # The _toggle_set is protected with a mutex to avoid that two threads are manipulating at the same
            # moment, thus resulting in data errors.
            acquired = self._toggle_mutex.acquire(blocking = True, timeout = COMMAND_TIMEOUT)
            if acquired:
                if (board_id, pin_nr) not in self._toggle_set:
                    if lock_if_free:
                        self._toggle_set.add((board_id, pin_nr))
                    keep_checking = False
                self._toggle_mutex.release()
            if (datetime.now() - checking_time).total_seconds()  > max (COMMAND_TIMEOUT, TOGGLEDELAY):
                keep_checking = False
                raise "Time-out error trying to acquire pin {} on board {}".format(board_id, pin_nr)

    def PinToggler(self, board_id, pin_nr, acquire_state = False):
        """
        The PinToggler is a separate process, run in a thread. This allows the main loop to continue processing other read/write requests.
        """
        # First make sure to do the bookkeeping.
        if self.CheckInitializeBoard(board_id):
            Process_Toggle = False

            try:
                self.WaitForPinToBeReleased(board_id, pin_nr, True)
                Process_Toggle = True
            except Exception as err:
                self._log.error(2, "Unable to toggle pin [0x{:0{}X}] on board [0x{:0{}X}]: Could not get pin free within [{}] seconds. Error Message: {}".format(pin_nr, 2, board_id, 2, COMMAND_TIMEOUT, err))
                Process_Toggle = False

            if Process_Toggle:
                self._log.info(2, "Toggling pin [0x{:0{}X}] on board [0x{:0{}X}]".format(pin_nr, 2, board_id, 2))
                # Default is that pin is toggled from low to high briefly.
                # If 'acquire_state' is set, the current state is assessed, and switched briefly to the "other" high/low state.
                if acquire_state:
                    current_state = self.GetI2CPin(board_id, pin_nr)
                else:
                    # Default is Low for current state and toggle to high to switch on e.g. a momentary switch.
                    current_state = 0x0

                if current_state == 0x0:
                    # Current state is low (0x0), and toggling needs to go to high briefly
                    self._log.info(2, "Toggling pin [0x{:0{}X}] on board [0x{:0{}X}] from LOW to HIGH".format(pin_nr, 2, board_id, 2))
                    self.SetI2CPin(board_id, pin_nr)
                    time.sleep(TOGGLEDELAY)
                    self.ClearI2CPin(board_id, pin_nr)
                    self._log.info(2, "Toggled pin [0x{:0{}X}] on board [0x{:0{}X}] back from HIGH to LOW".format(pin_nr, 2, board_id, 2))
                if current_state == 0x1:
                    # Current state is high (0x1 or more), and toggling needs to go to low briefly
                    self._log.info(2, "Toggling pin [0x{:0{}X}] on board [0x{:0{}X}] from HIGH to LOW".format(pin_nr, 2, board_id, 2))
                    self.ClearI2CPin(board_id, pin_nr)
                    time.sleep(TOGGLEDELAY)
                    self.SetI2CPin(board_id, pin_nr)
                    self._log.info(2, "Toggled pin [0x{:0{}X}] on board [0x{:0{}X}] back from LOW to HIGH".format(pin_nr, 2, board_id, 2))
                self._log.info(2, "Releasing (0x{:0{}X}, 0x{:0{}X}) from the Toggle set".format(board_id, 2, pin_nr, 2))
                # Make sure to remove the board/pin pair from the _toggle_set at the end, or the pin will be blocked for all other processing
                self._toggle_set.remove((board_id, pin_nr))
        else:
            self._log.error(2, "Toggling pin failed for [0x{:0{}X}] on board [0x{:0{}X}]: could not initialize board.".format(pin_nr, 2, board_id, 2))

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
    This XML Parameter Handler is used at boot time, so that the DIR pins of the different boards
    are set to their last remembered state. I.e. inputs are set back to inputs and outputs are 
    re-configured as outputs after the cold boot.
    During the processing, the XML file is constantly updated when the DIR (input vs. output) of 
    a pin changes.
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
                self.xml_pretty_print(self._confdata[0])
                outString = ET.tostring(self._confdata)
                outFile = open(self._filename,"w")
                outFile.write(outString.decode('ascii'))
                outFile.close()
                return_value = True
            except Exception as err:
                return_value = False
                # Disable further write attempts if the file cannot be written.
                self._use_config_file = False
                if LOG_LEVEL > 0:
                    print("Could not write parameter file [{}]. Error: {}".format(self._filename, err))
                self._log.info("Could not write parameter file [{}]. Error: {}".format(self._filename, err))
        return return_value

    def xml_pretty_print(self, element, level=0):
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
                self.xml_pretty_print(elem, level+1)
            if not element.tail or not element.tail.strip():
                element.tail = indent
        else:
            if level and (not element.tail or not element.tail.strip()):
                element.tail = indent

class LogThis():
    """
    A class for keeping track of the logging.
    In case that logging is requested, errors are tracked in the log file if the level is > 0. At high verbosity (level >= 3),
    all actions are logged for debugging purposes.
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
                if LOG_LEVEL > 0:
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

def InitBusAtBoot(the_log, xmldata, i2chandler):
    """
    If the program starts first time, pull the remembered boards from the XML config file. Set the proper input/output pin states to the last ones remembered.
    """
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
            # print error message to the systemctl log file
            if LOG_LEVEL == 2:
                print("Port [{}] of board [{}] should be set to [{}]".format(port_id, board_id, port.text))
            the_log.info(2, "Port [{}] of board [{}] should be set to [{}]".format(port_id, board_id, port.text))
            # Write the I/O state to the port
            if not(i2chandler.WriteI2CDir(board_id, port_id, port.text)):
                if LOG_LEVEL == 2:
                    print("That didn't work for board [{}]".format(board_id))
                    the_log.info(2, "That didn't work for board [{}]".format(board_id))
                # If that didn't work, the board may have been removed before booting. Remove it from the config file.
                xmldata.DeleteKey(board_id)

def main():
    """
    Main program function.
    """
    # Start a logger and provide info
    my_log = LogThis()
    my_log.info(1, "mcp23017server starting, running version [{}].".format(VERSION))
    # Parameter file for board input/output configurations
    my_log.info(2, "Creating XML Parameter Handler")
    xmldata = xmlParameterHandler(my_log)
    # Separate I2C handler, including a Mutex to make sure other clients are not messing with the I2C bus
    my_log.info(2, "Creating I2C Communication Handler")
    i2chandler = i2cCommunication(my_log)
    # Initialize the I2C bus at first run (manual run), or at boot time (if set up as a service).
    my_log.info(2, "Initializing I2C devices")
    InitBusAtBoot(my_log, xmldata, i2chandler)
    # Set up a new broker - this is the main part of the software.
    my_log.info(2, "Creating a Message Broker")
    mybroker = mcp23017broker(my_log, i2chandler, xmldata)
    # Process commands forever
    while True:
        mybroker.service_commands()
    my_log.error(1, "FATAL EXIT WITH ERROR [{}]".format(my_error_state))
    # Do a controlled exist with fail code. Trigger the OS to restart the service if configured.
    sys.exit(1)

if __name__ == "__main__":
    """
    Entry point when program is called from the command line.
    """
    main()
