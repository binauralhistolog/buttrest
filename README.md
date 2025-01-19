# ButtRest

ButtRest is a REST API that sits on top of [buttplug-py](https://github.com/Siege-Wizard/buttplug-py) and provides a stupidly obvious (HTTP+JSON) means of controlling a [remotely activated vibrator](https://buttplug.io).

There is no authentication or authorization around the REST API.  You may want to run ButtRest on a local RaspberryPi and make it accessible via [Tailscale](https://tailscale.com).

## Install anaconda (optional)

Python can be a bit odd, so you may want to use [miniconda](https://docs.anaconda.com/miniconda/) to isolate packages that you install.  Assuming that you're on MacOS:

```bash
brew install miniconda
conda config --set auto_activate_base false
```

Create a buttrest environment for conda:

```bash
conda create -n buttrest -y
conda activate buttrest
conda install pip -y
```

And then you can check out the project and install the requirements.  This will install  [buttplug-py](https://github.com/Siege-Wizard/buttplug-py), [sanic](https://sanic.dev/en/), pyld, and ujson.

```bash
git clone https://github.com/binauralhistolog/buttrest
pip install -r requirements.txt
```

## Intiface Central

Download [Intiface Central](https://github.com/intiface/intiface-central) and install it on the host that will be running ButtRest.  This is the websocket server that buttplug clients connect to.  You must have it up and running, and you must click the giant arrow on the top left of the GUI to start the server.

## Sanic

ButtRest runs on the [sanic](https://sanic.dev/en/) framework. To start the ButtRest server, run the following from the command line.

```bash
export BUTTREST_INTIFACE_URL="ws://127.0.0.1:12345" # intiface central's server address
sanic buttrest
```

## API Usage

All examples use `httpie`:

```bash
pip install httpie
```

See the [httpie documentation](https://httpie.io/docs/cli/explicit-json).

### Devices

See all devices:

```bash
http localhost:8000/devices
```

See device 0:

```bash
http localhost:8000/devices/0
```

### Sensors

See all sensors of device 0:

```bash
http localhost:8000/devices/0/sensors
```

See sensor 0 of device 0:

```bash
http localhost:8000/devices/0/sensors/0
```

Get the latest reading from sensor 0:

```bash
http localhost:8000/devices/0/sensors/0/read
```

### Actuators

See all actuators of device 0:

```bash
http localhost:8000/devices/0/actuators
```

See actuator 0:

```bash
http localhost:8000/devices/0/actuators/0
```

Turn off the actuator:

```bash
http POST localhost:8000/devices/0/actuators/0 intensity:=0.0
```

Set the actuator to max:

```bash
http POST localhost:8000/devices/0/actuators/0 intensity:=1.0
```

### Rotatory Actuators 

Set the rotatory actuator to speed=1.0 in a range (0.0-1.0) and clockwise=true:

```bash
http POST localhost:8000/devices/0/rotatory_actuators/0 speed:=1.0 clockwise:=true
```

### Linear Actuators

Set the rotatory actuator to duration (int) to 1000 milliseconds and position (float) to `0.5` for the position for the linear axis (0.0-1.0):

```bash
http POST localhost:8000/devices/0/linear_actuators/0 duration:=1000 position:=0.5
```

### Subscribable Sensors

Not implemented, sorry!