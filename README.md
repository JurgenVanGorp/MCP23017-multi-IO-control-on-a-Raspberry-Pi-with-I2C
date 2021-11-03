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
* It is also assumed that you are connecting to the RPi with SSH, e.g. with [WinSCP](https://winscp.net/)
* You need to have I2C configured on the RPi. If you haven't done that yet, [you can following this Adafruit guideline](https://learn.adafruit.com/adafruits-raspberry-pi-lesson-4-gpio-setup/configuring-i2c).

## Configuring and testing the I2C bus

Log on to the RPi with SSH and install i2c tools.

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

## Install smbus

**Info**: this software assumes you have the latest Python installed. You may want to [follow this guide](https://github.com/JurgenVanGorp/Home-Assistant-on-Raspberry-Pi-Native) to install the latest version of Python3.

The software in this repository is written in Python 3, and makes use of smbus. Install smbus as follows.

```
sudo apt install python3-smbus
sudo python3 -m pip install smbus
sudo python3 -m pip install smbus2
pip3 install RPI.GPIO
pip3 install adafruit-blinka sudo apt-get install python-smbus python3-smbus python-dev python3-dev i2c-tools
```

Configure the I2C parameters to be compatible with the MCP23017.

```
sudo nano /boot/config.txt
```

... and update the baudrate to e.g.

```python
dtparam=i2c_baudrate=400000
```

Then configure the core frequency in the config file with.

```
sudo nano /boot/config.txt
```

Add the following line.

```
core_freq=250
```

## IMPORTANT: install the redis database



```
```



```
```






Next topic: [Step 5: Controlling the MCP23017 from within Home-Assistant.](https://github.com/JurgenVanGorp/Step5-MCP23017-multi-I-O-Control-with-Raspberry-Pi-and-Home-Assistant)
