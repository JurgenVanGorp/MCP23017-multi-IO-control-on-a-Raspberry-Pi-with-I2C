# DRAFT - DO NOT USE YET

# MCP23017 multi I/O Control on a Raspberry Pi With I2C
This is a Raspberry Pi driver for controlling a MCP23017 I/O chip over I2C.

Previous topic: [Step 3: Setting up Home Assistant native on the Raspberry Pi.](https://github.com/JurgenVanGorp/Step3-Home-Assistant-on-Raspberry-Pi-Native)

## Introduction

An MCP23017 is a digital IC with 16 controllable Input-Output pins. The chip has three address bit that can be hard configured to one or zero. This allows for connecting up to eight MCP23017 ICs on the same I2C bus, allowing control of up to 128 input-output pins. The control can be done with the I2C port of e.g. a Raspberry Pi.
* Find the [data sheet of the MCP23017 here](https://www.adafruit.com/product/732).
* Find the [Raspberry Pi pin layout here](https://pinout.xyz/).
* If you want to experiment with the MCP23017 on a breadboard first, [you can find more information here](https://www.raspberrypi-spy.co.uk/2013/07/how-to-use-a-mcp23017-i2c-port-expander-with-the-raspberry-pi-part-1/).

If you want to install the Raspberry Pi (further also called RPi) from scratch, and with home assistant, you may want to install the Raspberry Pi [with this instruction upfront](https://github.com/JurgenVanGorp/Home-Assistant-on-Raspberry-Pi-Native).

## References

Next to the references given in the Introduction, Kudos go to the following developers and websites. 
* [Configuring and testing the I2C](https://learn.adafruit.com/adafruits-raspberry-pi-lesson-4-gpio-setup/configuring-i2c) on the RPi.
* This software gratefully makes use of the [in-memory redis database](https://redis.io). A good explanation of this terrific database [can be found here](https://pythontic.com/database/redis/hash%20-%20add%20and%20remove%20elements). The fulll command set is [published in this website](https://redis-py.readthedocs.io/en/stable/).

## Assumptions

* This checklist has been created on a Raspberry Pi Model 3 B+. Expectedly it works on other versions too.
* Please mind that this instruction was written on 30-Oct-2021. Time changes, and so do software versions.
* It is assumed that you already have an MCP23017 connected to the RPi. This is **not really** necesssary, but will make testing and debugging a lot easier.
* You need to have I2C configured on the RPi. If you haven't done that yet, [you can following this excellent guideline](https://learn.adafruit.com/adafruits-raspberry-pi-lesson-4-gpio-setup/configuring-i2c).

## Configuring and testing the I2C bus

Log on to the RPi with SSH and install i2c tools.

```
sudo apt-get update
sudo apt-get install i2c-tools
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

** Install smbus

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
