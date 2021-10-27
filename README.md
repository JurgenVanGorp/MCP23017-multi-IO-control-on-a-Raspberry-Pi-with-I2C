# MCP23017 multi I/O Control on a Raspberry Pi With I2C
This is a Raspberry Pi driver for controlling a MCP23017 I/O chip over I2C.

## Introduction

An MCP23017 is a digital IC with 16 controllable Input-Output pins. The chip has three address bit that can be hard configured to one or zero. This allows for connecting up to eight MCP23017 ICs on the same I2C bus, allowing control of up to 128 input-output pins. The control can be done with the I2C port of e.g. a Raspberry Pi.
* Find the [data sheet of the MCP23017 here](https://www.adafruit.com/product/732).
* Find the [Raspberry Pi pin layout here](https://pinout.xyz/).
* If you want to experiment with the MCP23017 on a breadboard first, [you can find more information here](https://www.raspberrypi-spy.co.uk/2013/07/how-to-use-a-mcp23017-i2c-port-expander-with-the-raspberry-pi-part-1/).

If you want to install the Raspberry Pi from scratch, and with home assistant, you may want to install the Raspberry Pi [with this instruction upfront](https://github.com/JurgenVanGorp/Home-Assistant-on-Raspberry-Pi-Native).

## References

Next to the references given in the Introduction, Kudos go to the following developers and websites. 
* 

## Assumptions

* This checklist has been created on a Raspberry Pi Model 3 B+. Expectedly it works on other versions too.
* Please mind that this instruction was written on 30-Oct-2021. Time changes, and so do software versions.
* It






