import sys
from time import sleep
import redis
import curses
from curses import wrapper
from datetime import datetime

VERSION = "1.00"

# Communications between Clients and the server happen through a Redis in-memory database
# so to limit the number of writes on the (SSD or microSD) storage. For larger implementations
# dozens to hundreds of requests can happen per second. Writing to disk would slow down the 
# process, and may damage the storage.
# Make sure to have Redis installed in the proper locations, e.g. also in the virtual python
# environments. The default is that Redis is installed on localhost (127.0.0.1).
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
# Offset where the images are drawn on the screen. please mind that information
# messages are displayed on the first lines, so don't make DELTA_Y lower than 11.
DELTA_X = 0
DELTA_Y = 12

# Acceptable Commands for controlling the I2C bus
# These are the commands you need to use to control the DIR register of the MCP23017, or
# for setting and clearing pins.

FINDBOARD = "IDENTIFY"        # Identify Board number, return 1 if found on the I2C bus
GETDIRBIT = "GETDBIT"         # Read the specific IO pin dir value (1 = input)
GETDIRREGISTER = "GETDIRREG"  # Read the full DIR register (low:1 or high:2)
SETDIRBIT = "SETDBIT"         # Set DIR pin to INPUT (1)
CLEARDIRBIT = "CLRDBIT"       # Clear DIR pin command to OUTPUT (0)
GETIOPIN = "GETPIN"           # Read the specific IO pin value
GETIOREGISTER = "GETIOREG"    # Read the full IO register (low:1 or high:2)
SETDATAPIN = "SETPIN"         # Set pin to High
CLEARDATAPIN = "CLRPIN"       # Set pin to low

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

# MCP23017 default parameters are that you can address the devices in the 0x20 to 0x2F 
# address space with the three selector pins. You can change these if you want to use 
# the software for other I2C devices.
MINBOARDID = 0x20        # Minimum I2C address for MCP23017
MAXBOARDID = 0x27        # Maximum I2C address for MCP23017

class CommandsBroker:
  """
  The CommandsBroker class is the communication line to the mcp23017server. Communications are done through the Redis database pipe.
  """
  def __init__(self):
    # Commands have id   datetime.now().strftime("%d-%b-%Y %H:%M:%S.%f")}, i.e. the primary key is a timestamp. 
    # Commands given at exactly the same time, will overwrite each other, but this is not expected to happen.
    # The commands table is then formatted as (all fields are TEXT, even if formatted as "0xff" !!)
    # id, command TEXT, boardnr TEXT DEFAULT '0x00', pinnr TEXT DEFAULT '0x00', datavalue TEXT DEFAULT '0x00'
    self._commands = None
    # Responses have id   datetime.now().strftime("%d-%b-%Y %H:%M:%S.%f")}, i.e. the primary key is a timestamp. 
    # The Responses table is then formatted as (all fields are TEXT, even if formatted as "0xff" !!)
    # id, command_id TEXT, datavalue TEXT, response TEXT
    self._responses = None
    self.errormessage = self.OpenAndVerifyDatabase()
    if (self.errormessage == ""):
      self.RedisDBInitialized = True
    else:
      self.RedisDBInitialized = False

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
          self._commands = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=0)
          # Redis database [1] is for responses from the server so the clients.
          nowTrying = "Responses"
          self._responses = redis.StrictRedis(host=REDIS_HOST, port=REDIS_PORT, db=1)
      except OSError as err:
          # Capturing OS error.
          return "FATAL OS ERROR. Could not open [{}] database. This program is now exiting with error [{}].".format(nowTrying, err)
      except:
          # Capturing all other errors.
          return "FATAL UNEXPECTED ERROR. Could not open [{}] database. This program is now exiting with error [{}].".format(nowTrying, sys.exc_info()[0])
      
      # Do a dummy write to the Commands database, as verification that the database is fully up and running.
      try:
          # Remember: fields are 
          #    id, command TEXT, boardnr TEXT DEFAULT '0x00', pinnr TEXT DEFAULT '0x00', datavalue TEXT DEFAULT '0x00'
          id =  (datetime.now() - datetime.utcfromtimestamp(0)).total_seconds()
          datamap = {'command':'dummycommand', 'boardnr':0x00, 'pinnr':0xff, 'datavalue':0x00}
          # Write the info to the Redis database
          self._commands.hset(id, None, None, datamap)
          # Set expiration to 1 second, after which Redis will automatically delete the record
          self._commands.expire(id, 1)
      except:
          # Capturing all errors.
          return "FATAL UNEXPECTED ERROR. Could not read and/or write the [Commands] database. This program is now exiting with error [{}].".format(sys.exc_info()[0])

      # Next, do a dummy write to the Responses database, as verification that the database is fully up and running.
      try:
          # Remember: fields are 
          #    id, command_id TEXT, datavalue TEXT, response TEXT
          id =  (datetime.now() - datetime.utcfromtimestamp(0)).total_seconds()
          datamap = {'datavalue':0x00, 'response':'OK'}
          # Write the info to the Redis database
          self._responses.hset(id, None, None, datamap)
          # Set expiration to 1 second, after which Redis will automatically delete the record
          self._responses.expire(id, 1)
      except:
          # Capturing all errors.
          return "FATAL UNEXPECTED ERROR. Could not read and/or write the [Responses] database. This program is now exiting with error [{}].".format(sys.exc_info()[0])
      # We got here, so return zero error message.
      return ""

  def SendCommand(self, whichCommand, board_id, pin_id = 0x00):
      """
      Send a new command to the mcp23017server through a Redis database record.
      The commands will get a time-out, to avoid that e.g. a button pushed now, is only processed hours later.
      Response times are expected to be in the order of (fractions of) seconds.
      """
      # Prepare new id based on timestamp. Since this is up to the milliseconds, the ID is expected to be unique
      id = (datetime.now() - datetime.utcfromtimestamp(0)).total_seconds()
      # Create data map
      mapping = {'command':whichCommand, 'boardnr':board_id, 'pinnr':pin_id}
      # Expiration in the Redis database can be set already. Use the software expiration with some grace period.
      # Expiration must be an rounded integer, or Redis will complain.
      expiration = round(COMMAND_TIMEOUT + 1)
      # Now send the command to the Redis in-memory database
      self._commands.hset(id, None, None, mapping)
      # Command must self-delete within the expiration period. Redis can take care.
      self._commands.expire(id, expiration)
      # The timestamp is also the id of the command (needed for listening to the response)
      return id

  def WaitForReturn(self, command_id):
      """
      Wait for a response to come back from the mcp23017server, once the command has been processed on the
      I2C bus. If the waiting is too long (> COMMAND_TIMEOUT), cancel the operation and return an error.
      """
      answer = None
      # If no timely answer, then cancel anyway. So, keep track of when we started.
      checking_time = datetime.now()
      while answer == None:
          # request the data from the Redis database, based on the Command ID.
          datafetch = self._responses.hgetall(command_id)
          # Verify if a response is available.
          if len(datafetch) > 0:
              # Do data verification, to cover for crippled data entries without crashing the software.
              try:
                  datavalue = datafetch[b'datavalue'].decode('ascii')
              except:
                  datavalue = 0x00

              try:
                  response = datafetch[b'response'].decode('ascii')
              except:
                  response = "Error Parsing mcp23017server data."

              answer = (datavalue, response)
          if (datetime.now() - checking_time).total_seconds()  > COMMAND_TIMEOUT:
              answer = (0x00, "Time-out error trying to get result from server for Command ID {}".format(command_id))
      return answer

  def ProcessCommand(self, whichCommand, board_id, pin_id = 0x00):
      """
      The ProcessCommand function is a combination of sending the Command to the mcp23017server host, and 
      waiting for the respone back.
      """
      retval = -1
      # First send the command to the server
      command_id = self.SendCommand(whichCommand, board_id, pin_id)
      # Then wait for the response back
      response = self.WaitForReturn(command_id)
      # A good command will result in an "OK" to come back from the server.
      if response[1].strip().upper() == 'OK':
          # OK Received, now process the data value that was sent back.
          retval = response[0]
          if(isinstance(retval,str)):
              if len(retval) == 0:
                  retval = 0x00
              else:
                  try:
                      if 'x' in retval:
                          retval = int(retval, 16)
                      else:
                          retval = int(retval, 10)
                  except:
                      # wrong type of data received
                      retval = "Error when processing return value. Received value that I could not parse: [{}]".format(response[0])
      else:
          retval = "Error when processing pin '0x{:02X}' on board '0x{:02X}'. Error Received: {}".format(board_id, pin_id, response[1])
      return retval

################################################################################################
rdb = CommandsBroker()
################################################################################################

class mcp23017addrpin:
  """
  A class that handles the graphical output of a single _ADDRESS_ pin, and also handles mouse clicks. 
  The class also holds the pin's state.
  """
  def __init__(self, v_position, mcp_pin_number, my_name = ''):
    self.name = my_name
    self.pinval = '0'
    self._hpos = DELTA_X + 45
    self._vpos = DELTA_Y + v_position
    self.pin_number = mcp_pin_number

  def draw(self, canvas):
    canvas.addstr(self._vpos, self._hpos, '[{}]'.format(self.pinval), curses.color_pair(3) if (self.pinval == '1') else curses.color_pair(2))

  def EvaluateClick(self, mouse_x, mouse_y, board_id):
    if (self._vpos == mouse_y):
      ref_x = mouse_x - 45
      if (ref_x >= 0) and (ref_x <= 2):
        if (self.pinval == '0'):
          self.pinval = '1'
        else:
          self.pinval = '0'

class mcp23017datapin:
  """
  A class that handles the graphical output of a single _DATA_ pin, and also handles mouse clicks. 
  The class also holds the pin's state.
  """
  def __init__(self, left_or_right, v_position, mcp_pin_number, my_name = ''):
    self.name = my_name
    self.pindir = ' IN'
    self.pinval = 'Z'
    self._leftright = left_or_right
    self._hpos = DELTA_X
    self._vpos = DELTA_Y + v_position
    self.pin_number = mcp_pin_number

  def draw(self, canvas):
    if self._leftright == 'L':
      hdirpos = self._hpos + 7
      hvalpos = self._hpos + 3
    else:
      hdirpos = self._hpos + 45
      hvalpos = self._hpos + 51

    # Note that the pin Direction (IN or OUT) are 'drawn' with [xx] to denote that the can be changed by clicking on them.
    # The Pin Value is set to [x] in case the pin direction is 'OUT'. An input is read from the I2C bus.
    # Zero pinval values and 'OUT' pin direction values are shown in red.
    canvas.addstr(self._vpos, hdirpos, '[{}]'.format(self.pindir), curses.color_pair(3) if (self.pindir == ' IN') else curses.color_pair(2))
    if self.pinval == '0':
      if self.pindir == ' IN':
        canvas.addstr(self._vpos, hvalpos, ' {} '.format(self.pinval), curses.color_pair(2))
      else:
        canvas.addstr(self._vpos, hvalpos, '[{}]'.format(self.pinval), curses.color_pair(2))
    elif self.pinval == '1':
      if self.pindir == ' IN':
        canvas.addstr(self._vpos, hvalpos, ' {} '.format(self.pinval), curses.color_pair(3))
      else:
        canvas.addstr(self._vpos, hvalpos, '[{}]'.format(self.pinval), curses.color_pair(3))
    else:
      canvas.addstr(self._vpos, hvalpos, ' {} '.format(self.pinval), curses.color_pair(0))

  def EvaluateClick(self, mouse_x, mouse_y, board_id):
    if (self._vpos == mouse_y):
      # Evaluate if [Val] clicked
      if (self.pindir == 'OUT'):
        if (self._leftright == 'L'):
          ref_x = mouse_x - 3
        else:
          ref_x = mouse_x - 51
        if (ref_x >= 0) and (ref_x <= 4):
          if (self.pinval == '1'):
            self.pinval = '0'
            rdb.SendCommand("CLRPIN", board_id, self.pin_number)
          else:
            self.pinval = '1'
            rdb.SendCommand("SETPIN", board_id, self.pin_number)
      # Evaluate if [Pin] clicked
      if (self._leftright == 'L'):
        ref_x = mouse_x - 7
      else:
        ref_x = mouse_x - 45
      if (ref_x >= 0) and (ref_x <= 4):
        if (self.pindir == ' IN'):
          self.pindir = 'OUT'
          self.pinval = '0'
          rdb.SendCommand("CLRDBIT", board_id, self.pin_number)
          rdb.SendCommand("CLRPIN", board_id, self.pin_number)
        else:
          self.pindir = ' IN'
          rdb.SendCommand("SETDBIT", board_id, self.pin_number)

class mcp23017:
  def __init__(self):
    self.key = 0
    self.board_id = 0x20
    # Initialize all pins that can be changed by the user
    self._pins = []
    self._pins.append(mcp23017datapin('L',  2,  8, 'GPB0'))
    self._pins.append(mcp23017datapin('L',  4,  9, 'GPB1'))
    self._pins.append(mcp23017datapin('L',  6, 10, 'GPB2'))
    self._pins.append(mcp23017datapin('L',  8, 11, 'GPB3'))
    self._pins.append(mcp23017datapin('L', 10, 12, 'GPB4'))
    self._pins.append(mcp23017datapin('L', 12, 13, 'GPB5'))
    self._pins.append(mcp23017datapin('L', 14, 14, 'GPB6'))
    self._pins.append(mcp23017datapin('L', 16, 15, 'GPB7'))
    self._pins.append(mcp23017datapin('R',  2,  7, 'GPA7'))
    self._pins.append(mcp23017datapin('R',  4,  6, 'GPA6'))
    self._pins.append(mcp23017datapin('R',  6,  5, 'GPA5'))
    self._pins.append(mcp23017datapin('R',  8,  4, 'GPA4'))
    self._pins.append(mcp23017datapin('R', 10,  3, 'GPA3'))
    self._pins.append(mcp23017datapin('R', 12,  2, 'GPA2'))
    self._pins.append(mcp23017datapin('R', 14,  1, 'GPA1'))
    self._pins.append(mcp23017datapin('R', 16,  0, 'GPA0'))

    self._pins.append(mcp23017addrpin(24, 17, 'A2'))
    self._pins.append(mcp23017addrpin(26, 16, 'A1'))
    self._pins.append(mcp23017addrpin(28, 15, 'A0'))
    
    # All possible MCP23017 boards on the I2C can go from 0x20 to 0x27
    self._boards = []
    for i in range(MINBOARDID, MAXBOARDID + 1):
      self._boards.append(i)
    self.boardsfound = {}

  def DrawPins(self, canvas):
    # Draw the value of each individual pin
    for aPin in self._pins:
      aPin.draw(canvas)

  def ProcessMouseClick(self, canvas, mouse_x, mouse_y):
    for aPin in self._pins:
      aPin.EvaluateClick(mouse_x, mouse_y, self.board_id)
      aPin.draw(canvas)
    # Evaluate if one of the Address pins has been clicked. If so, change the Board_ID to the new value.
    self.board_id = 0x20
    for aPin in self._pins:
      if (aPin.name == 'A0') and (aPin.pinval == '1'):
        self.board_id += 1
      if (aPin.name == 'A1') and (aPin.pinval == '1'):
        self.board_id += 2
      if (aPin.name == 'A2') and (aPin.pinval == '1'):
        self.board_id += 4
  
  def BoardIsOnI2C(self, board_id):
      retval = rdb.ProcessCommand("IDENTIFY", board_id, 0x00)
      # Verify in inputs are given as hex. Convert to int if so
      if(isinstance(retval,str)):
          retval = int(retval, 16)
      else:
          retval = int(retval)
      if retval == 1:
        return True
      else:
        return False

  def ScanBoards(self):
    # Scan which boards can be found on the I2C bus
    self.boardsfound.clear()
    for board_id in self._boards:
      if rdb.ProcessCommand("IDENTIFY", board_id, 0x00) == 1:
        self.boardsfound[board_id] = 'UP'
      else:
        self.boardsfound[board_id] = '--'

  def ScanPins(self):
    # Scan the pins of the active MCP23017, but only do this for the address pins.
    for aPin in self._pins:
      if aPin.name not in ('A0', 'A1', 'A2'):
        # Get the Direction (IN or OUT) of the pin
        if (rdb.ProcessCommand("GETDBIT", self.board_id, aPin.pin_number) == 1):
          aPin.pindir = ' IN'
        else:
          aPin.pindir = 'OUT'
        # Get the value of the pin (Hi/1 or Lo/0)
        if (rdb.ProcessCommand("GETPIN", self.board_id, aPin.pin_number) == 1):
          aPin.pinval = '1'
        else:
          aPin.pinval = '0'

################################################################################################
mcp=mcp23017()
################################################################################################

def WrappedDraw(stdscr):
  """
  Essentially this is the main routine. This routines draws the MCP23017 on the screen.
  The routine is wrapped in a curses wrapper so that the screen is not messed up after stopping
  the program. Curses can leave the screen in a very messy state otherwise.
  """
  curses.curs_set(0)
  curses.mousemask(curses.ALL_MOUSE_EVENTS)
  curses.start_color()
  curses.use_default_colors()
  for i in range(0, curses.COLORS):
    curses.init_pair(i + 1, i, -1)
  mcp.key = 0
  while (mcp.key != 27):  # Key 27 is the [Esc] key.
    stdscr.clear()
    # First draw the instruction on the screen
    stdscr.addstr(1,  DELTA_X, 'INSTRUCTIONS:')
    stdscr.addstr(2,  DELTA_X, '   Type [Esc]ape to stop this program.')
    stdscr.addstr(3,  DELTA_X, '   Click on values between [brackets] to change the value.')
    # Draw system information on the screen
    stdscr.addstr(4,  DELTA_X, 'INFORMATION:')
    if rdb.RedisDBInitialized:
      stdscr.addstr(5,  DELTA_X, '    Redis database initialized.', curses.color_pair(3))
    else:
      stdscr.addstr(5,  DELTA_X, '    Redis database failed.', curses.color_pair(2))
    if mcp.BoardIsOnI2C(mcp.board_id):
      stdscr.addstr(6,  DELTA_X, '    MCP23017 0x{:02X} found on I2C'.format(mcp.board_id), curses.color_pair(3))
    else:
      stdscr.addstr(6,  DELTA_X, '    MCP23017 0x{:02X} not found on I2C'.format(mcp.board_id), curses.color_pair(2))    
    # Scan the I2C bus and inform which MCP23017 devices were found on the bus.
    stdscr.addstr(7,  DELTA_X, 'MCP23017 Boards found on I2C:')    
    mcp.ScanBoards()
    idcntr = 4
    for i in range (0, len(mcp.boardsfound)):
      stdscr.addstr(8,  DELTA_X + idcntr, '0x{:02X}'.format(list(mcp.boardsfound.keys())[i]))
      stdscr.addstr(9,  DELTA_X + idcntr, '{:03b}'.format(list(mcp.boardsfound.keys())[i] - 0x20))
      stdscr.addstr(10,  DELTA_X + idcntr + 1, '{}'.format(list(mcp.boardsfound.values())[i]))
      idcntr += 5
    # Now, start drawing the graphical static part.
    stdscr.addstr(DELTA_Y + 0,  DELTA_X, '                    +-------v-------+        ')
    stdscr.addstr(DELTA_Y + 1,  DELTA_X, '                    ![o] MCP23017   !        ')
    stdscr.addstr(DELTA_Y + 2,  DELTA_X, '             GPB0 <=+ 01         28 +=> GPA7         ')
    stdscr.addstr(DELTA_Y + 3,  DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 4,  DELTA_X, '             GPB1 <=+ 02         27 +=> GPA6         ')
    stdscr.addstr(DELTA_Y + 5,  DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 6,  DELTA_X, '             GPB2 <=+ 03         26 +=> GPA5         ')
    stdscr.addstr(DELTA_Y + 7,  DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 8,  DELTA_X, '             GPB3 <=+ 04         25 +=> GPA4         ')
    stdscr.addstr(DELTA_Y + 9,  DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 10, DELTA_X, '             GPB4 <=+ 05         24 +=> GPA3         ')
    stdscr.addstr(DELTA_Y + 11, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 12, DELTA_X, '             GPB5 <=+ 06         23 +=> GPA2         ')
    stdscr.addstr(DELTA_Y + 13, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 14, DELTA_X, '             GPB6 <=+ 07         22 +=> GPA1         ')
    stdscr.addstr(DELTA_Y + 15, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 16, DELTA_X, '             GPB7 <=+ 08         21 +=> GPA0         ')
    stdscr.addstr(DELTA_Y + 17, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 18, DELTA_X, '         3.3V VDD <=+ 09         20 +=> INTA  GND')
    stdscr.addstr(DELTA_Y + 19, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 20, DELTA_X, '         GND  VSS <=+ 10         19 +=> INTB  GND')
    stdscr.addstr(DELTA_Y + 21, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 22, DELTA_X, '              N/C <=+ 11         18 +=> /RESET')
    stdscr.addstr(DELTA_Y + 23, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 24, DELTA_X, '              SCK <=+ 12         17 +=> A2       ')
    stdscr.addstr(DELTA_Y + 25, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 26, DELTA_X, '              SDA <=+ 13         16 +=> A1       ')
    stdscr.addstr(DELTA_Y + 27, DELTA_X, '                    !               !        ')
    stdscr.addstr(DELTA_Y + 28, DELTA_X, '              N/C <=+ 14         15 +=> A0       ')
    stdscr.addstr(DELTA_Y + 29, DELTA_X, '                    +---------------+  ')
    stdscr.addstr(DELTA_Y + 30,  DELTA_X, '')

    # Draw the graphical dynamic part.
    mcp.ScanPins()
    mcp.DrawPins(stdscr)
    stdscr.refresh()

    sleep(0.1)
    stdscr.nodelay(1)
    mcp.key = stdscr.getch()
    if mcp.key == curses.KEY_MOUSE:
      _, mx, my, _, _ = curses.getmouse()
      mcp.ProcessMouseClick(stdscr, mx, my)

def main():
  wrapper(WrappedDraw)

if __name__ == "__main__":
    """
    Entry point when program is called from the command line.
    """
    main()
