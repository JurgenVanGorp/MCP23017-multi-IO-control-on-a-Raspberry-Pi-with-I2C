# DRAFT - DO NOT USE YET
Guide creation date: 30-Oct-2021

# MCP23017 multi I/O Control on a Raspberry Pi With I2C
This is a Raspberry Pi driver for controlling a MCP23017 I/O chip over I2C.

Previous topic: [Step 3: Setting up Home Assistant native on the Raspberry Pi.](https://github.com/JurgenVanGorp/Step3-Home-Assistant-on-Raspberry-Pi-Native)

## Introduction

An MCP23017 is a digital IC with 16 controllable Input-Output pins. The chip has three address bit that can be hard configured to one or zero. This allows for connecting up to eight MCP23017 ICs on the same I2C bus, allowing control of up to 128 input-output pins. The control can be done with the I2C port of e.g. a Raspberry Pi.

This software is an intermediate layer between the MCP23017 IC and different clients. You can send commands for setting pins to input or output, controlling and reading pins, and for toggling a pin momentary. The latter feature will be used for the hardware developments that are demonstrated in the next steps of this series of guides. The use of the software is explained further.

**REMARK**: The commands sent, are buffered but with a limited lifetime (default: 1.5 seconds, configurable). If multiple commands are received in a short time period, the commands will be stacked and serialized for sending to the MCP23017 over the I2C bus. If, for some reason, the I2C bus is overloaded or the hardware is irresponsive, the commmands will just be deleted after 1.5 seconds without sending them to the MCP23017. This behaviour has been implemented for home domotics use. If your hardware is irresponsive for whatever reason, people tend to push buttons repeatedly, hoping that the *darn* thing will work after pushing the button ten times. You don't want all of these on/off commands to be stacked and sent to your MCP23017 hardware drivers when they come back online. I.e.: if you push the light switch, and the light doesn't switch on within a second or so, you know something is wrong and you'll push the button once more. The first command can then be discarded.

Please mind that this software is meant for use with a Raspberry Pi. If you want to install the Raspberry Pi (further also called RPi) from scratch, and with home assistant, you may want to install the Raspberry Pi [with this instruction upfront](https://github.com/JurgenVanGorp/Home-Assistant-on-Raspberry-Pi-Native).

## References

This implementation of the MCP23017 on a Raspberry Pi gratefully makes use of the [Redis in-memory database](https://redis.io) for the communication between the MCP23017 IC and the different clients. An in-memory database was chosen to avoid a high read/write load on the delicate SDCard. Redis also provides the feature that data can be set to expire automatically, which turned out to be very useful for this home automation implementation. It also means that the commands are lost in memory when the RPi is switched off, but this too is wanted behaviour.

A good explanation of the Redis database [can be found here](https://pythontic.com/database/redis/hash%20-%20add%20and%20remove%20elements). The fulll command set is [published in this website](https://redis-py.readthedocs.io/en/stable/).

Other useful references are the following.
* Find the [data sheet of the MCP23017 here](https://www.adafruit.com/product/732).
* Find the [Raspberry Pi pin layout here](https://pinout.xyz/).
* If you want to experiment with the MCP23017 on a breadboard first, [you can find more information here](https://www.raspberrypi-spy.co.uk/2013/07/how-to-use-a-mcp23017-i2c-port-expander-with-the-raspberry-pi-part-1/).
* If you want to learn more about using I2C on the Raspberry Pi, Adafruit provides an excellent overview: [Configuring and testing the I2C on the RPi.](https://learn.adafruit.com/adafruits-raspberry-pi-lesson-4-gpio-setup/configuring-i2c)

## Assumptions

* This checklist has been created on a Raspberry Pi Model 3 B+. Expectedly it works on other Model 3 versions and on the RPi 4.
* Please mind that this instruction was written on 30-Oct-2021. Time changes, and so do software versions.
* It is assumed that you already have an MCP23017 connected to the RPi. This is **not really** necesssary, but will make testing and debugging a lot easier.
* It is also assumed that you are connecting to the RPi with SSH, e.g. with [PuTTY](https://www.putty.org/)
* You need to have I2C configured on the RPi. If you haven't done that yet, [you can following this Adafruit guideline](https://learn.adafruit.com/adafruits-raspberry-pi-lesson-4-gpio-setup/configuring-i2c).

## 1. Configuring and testing the I2C bus

The easiest way to enable I2C is with raspi-config:

```
sudo raspi-config
```

Then enable I2C in the *Interface Options* section.

To test the I2C connection, connect at least one MCP23017 device to the Rpi. Log on to the RPi with SSH and install i2c tools.

```
sudo apt install i2c-tools -y
```

Verify that you can see the MCP23017 on the I2C bus.

```
sudo i2cdetect -y 1
```

You should see something like this.  

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: 20 21 22 -- 24 25 26 27 -- -- -- -- -- -- -- --
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
40: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
60: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
70: -- -- -- -- -- -- -- --
```

In this **example** seven MCP23017 devices were connected on the I2C bus. A first group was configured with the address bits set to 000, 001 and 010. A second group was configured with the binary bits set to 100, 101, 110 and 111. On the I2C bus this reads as resp. 0x20, 0x21, 0x22 and 0x24, 0x25, 0x26, 0x27. 

If you have connected only one MCP23017, you will probably only see something like.

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:          -- -- -- -- -- -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: 20 -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
40: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
60: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
70: -- -- -- -- -- -- -- --
```

If you're not seeing anything on the bus, you can try limiting the bus speed, and explicitly set the configuration. Edit the config.txt file as follows.

```
sudo nano /boot/config.txt
```

Then go down the lines with *dtparam=i2c_*. In that section update, or add the following:

```
# Enable I2C bus number 1, disable I2C bus 0
dtparam=i2c_vc=off
dtparam=i2c_arm=on
# Switch off the spi and i2s communication buses
dtparam=spi=off
dtparam=i2s=off
# Set the baudrate on the I2C bus
dtparam=i2c_baudrate=400000
# Stabilize the frequency on Raspb B and B+
core_freq=250
```

Finalize the new settings with rebooting, and test the i2cdetect again.

```
sudo reboot
```

## 2. Install and test the Redis database

Let's start again with a :

```
sudo apt update -y
sudo apt upgrade -y
```

Then install Redis and its components.

```
sudo apt install libhiredis0.14 liblua5.1-0 lua-bitop lua-cjson redis-server redis-tools ruby-redis -y
```

Redis should work already in this stage. Test it with the command:

```
redis-cli ping
```

which should result in a simple reply:

```
PONG
```

We now need to make Redis available for python3 too. Do this with:

```
python3 -m pip install redis
```

Let's test if Redis now works in python. Let's first create a test file with:

```
nano redistest.py
```

In that file enter the following little program, which will perform the same type of ping test.

```python
import redis

try:
  r = redis.StrictRedis(host='localhost', port=6379, db=0)
  try:
    r.ping()
    print("Successfully pinged redis.")
  except (redis.exceptions.ConnectionError, ConnectionRefusedError):
    print("Redis connection error.")
except:
  print("Could not create redis object.")
```

Save with Ctrl-S and exit with Ctrl-X. Now test redis with.

```
python3 redistest.py
```

**IMPORTANT**: If you [installed HomeAssistant in the previous step](https://github.com/JurgenVanGorp/Home-Assistant-on-Raspberry-Pi-Native), you will need to install redis for python also in the Home Assistant virtual environment as follows.

```
cd /srv/homeassistant/
sudo -u homeassistant -H -s
cd /srv/homeassistant/
source bin/activate
python3 -m pip install redis
exit
```

Just before the *exit* you can create the same *redistest.py* file and redo the test, if you like.

## 3. Install the mcp23017server Software and Service

The MCP23017server program makes use of e.g. smbus for the I2C communication. So, let's first add the library.

```
sudo apt install python3-smbus -y
```

Under the assumption that you have logged on with the *pi* account, your home folder would be */home/pi*. Let's create a separate (hidden) folder for the MCP23017 server, and download the necessary server files.

```
mkdir /home/pi/.mcp23017server
cd /home/pi/.mcp23017server
wget -L https://raw.githubusercontent.com/JurgenVanGorp/MCP23017-multi-IO-control-on-a-Raspberry-Pi-with-I2C/main/mcp23017control/mcp23017server.py
sudo chmod +x mcp23017server.py
```

To make the service start at boot time, create a service for starting the MCP23017server automatically.

```
sudo nano /etc/systemd/system/mcp23017server.service
```

In this file, enter the following lines.

```
[Unit]
Description=MCP23017 Server
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart= /usr/bin/python3 /home/pi/.mcp23017server/mcp23017server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Save with Ctrl-S and exit with Ctrl-X. Then enable, start and verify the service with:

```
sudo systemctl daemon-reload
sudo systemctl enable mcp23017server.service
sudo systemctl start mcp23017server.service
sudo reboot

sudo systemctl status mcp23017server.service
```

If all goes well, you should see something like

```javascript
● mcp23017server.service - MCP23017 Server
   Loaded: loaded (/etc/systemd/system/mcp23017server.service; enabled; vendor preset: enabled)
   Active: active (running) since Thu 2021-11-04 21:25:05 CET; 11min ago
 Main PID: 521 (python3)
    Tasks: 1 (limit: 2059)
   CGroup: /system.slice/mcp23017server.service
           └─521 /usr/bin/python3 /home/pi/.mcp23017server/mcp23017server.py

Nov 04 21:25:05 HA-Garage systemd[1]: Started MCP23017 Server.
```


If you don't see this, look carefully at the error messages, and take appropriate action. If all looks well, reboot the RPi and verify the service again.

```
sudo reboot
```

Next topic: [Step 4: Controlling the MCP23017 from within Home-Assistant.](https://github.com/JurgenVanGorp/MCP23017-multi-I-O-Control-with-Raspberry-Pi-and-Home-Assistant)
