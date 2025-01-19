from sanic import Sanic, SanicException, BadRequest, ServerError
from sanic.response import text
from sanic.log import logger

import ujson

from buttplug import Client, WebsocketConnector, ProtocolSpec, Device, ButtplugError
from buttplug.client import Actuator, Sensor

import asyncio
from typing import Union

Number = Union[int, float]

class ButtPlugConnectionError(SanicException):
    status_code = 502 
    message = "Client Connection Failed"

app = Sanic("ButtRest", env_prefix='BUTTREST_')
app.config.CLIENT_NAME = "ButtRest"

#######################
# Server Lifecycle
    
@app.before_server_start
async def before_server_start(app):
    client = Client(app.config.CLIENT_NAME, ProtocolSpec.v3)
    connector = WebsocketConnector(app.config.INTIFACE_URL, logger=client.logger)

    # Make buttplug logging level match sanic logging level
    client.logger.level = logger.level

    await client.connect(connector)
    await client.start_scanning()
    await asyncio.sleep(3)
    await client.stop_scanning()

    logger.info(f'Registered devices: {client.devices}')
    app.ctx.client = client

@app.after_server_stop
async def after_server_stop(app):
    await app.ctx.client.disconnect()

#######################
# Handlers

@app.get("/healthz")
async def health_check(request):
    return text("OK")

@app.get("/")
async def index(request):
    return text("Hello, world.")

@app.get("/devices")
async def devices_get(request):
    client: Client = get_client()
    devices = [render_device(device) for device in client.devices.values()]
    return text(ujson.dumps(devices, indent=2))

@app.get("/devices/<device_id:int>")
async def device_get(request, device_id: int):
    device = get_device(device_id)
    device_resource = render_device(device)
    return text(ujson.dumps(device_resource, indent=2))

@app.get("/devices/<device_id:int>/sensors")
async def sensors_get(request, device_id: int):
    device = get_device(device_id)
    sensor_resources = [render_sensor(device_id, s) for s in device.sensors]
    return text(ujson.dumps(sensor_resources, indent=2))

@app.get("/devices/<device_id:int>/sensors/<sensor_id:int>")
async def sensor_get(request, device_id: int, sensor_id: int):
    sensor = get_sensor(device_id, sensor_id)
    sensor_resource = render_sensor(device_id, sensor)
    return text(ujson.dumps(sensor_resource, indent=2))

@app.get("/devices/<device_id:int>/sensors/<sensor_id:int>/read")
async def sensor_reading_get(request, device_id: int, sensor_id: int):
    sensor = get_sensor(device_id, sensor_id)
    sensor_reading = await render_sensor_reading(device_id, sensor)
    return text(ujson.dumps(sensor_reading, indent=2))

def subscribable_sensor_get():
    return text("Render subscribable sensor")

@app.get("/devices/<device_id:int>/actuators")
async def actuators_get(request, device_id: int):
    device = get_device(device_id)
    actuator_resources = [render_actuator(device_id, a) for a in device.actuators]
    return text(ujson.dumps(actuator_resources, indent=2))

@app.get("/devices/<device_id:int>/actuators/<actuator_id:int>")
async def actuator_get(request, device_id: int, actuator_id: int):
    actuator = get_actuator(device_id, actuator_id)
    actuator_resource = render_actuator(device_id, actuator)
    return text(ujson.dumps(actuator_resource, indent=2))

@app.post("/devices/<device_id:int>/actuators/<actuator_id:int>")
async def actuator_post(request, device_id: int, actuator_id: int):
    actuator = get_actuator(device_id, actuator_id)
    body = request.json
    intensity = body["intensity"]
    logger.info(f"actuator_post: {intensity}")
    try:
        await actuator.command(intensity)
        return text("OK")
    except ButtplugError as error:
        raise ServerError(f"{error}")

@app.get("/devices/<device_id:int>/linear_actuators")
async def linear_actuators_get(request, device_id: int):
    device = get_device(device_id)
    actuator_resources = [render_actuator(device_id, a) for a in device.linear_actuators]
    return text(ujson.dumps(actuator_resources, indent=2))

@app.get("/devices/<device_id:int>/linear_actuators/<actuator_id:int>")
async def linear_actuator_get(request, device_id: int, actuator_id: int):
    actuator = get_linear_actuator(device_id, actuator_id)
    actuator_resource = render_actuator(device_id, actuator)
    return text(ujson.dumps(actuator_resource, indent=2))

@app.post("/devices/<device_id:int>/linear_actuators/<actuator_id:int>")
async def linear_actuator_post(request, device_id: int, actuator_id: int):
    actuator = get_linear_actuator(device_id, actuator_id)
    body = request.json
    duration = int(body["duration"])
    position = float(body["position"])
    try:
        await actuator.command(duration, position)
        return text("OK")
    except ButtplugError as error:
        raise ServerError(f"{error}")

@app.get("/devices/<device_id:int>/rotatory_actuators")
async def rotatory_actuators_get(request, device_id: int):
    device = get_device(device_id)
    actuator_resources = [render_actuator(device_id, a) for a in device.linear_actuators]
    return text(ujson.dumps(actuator_resources, indent=2))

@app.get("/devices/<device_id:int>/rotatory_actuators/<actuator_id:int>")
async def rotatory_actuator_get(request, device_id: int, actuator_id: int):
    actuator = get_rotatory_actuator(device_id, actuator_id)
    actuator_resource = render_actuator(device_id, actuator)
    return text(ujson.dumps(actuator_resource, indent=2))

@app.post("/devices/<device_id:int>/rotatory_actuators/<actuator_id:int>")
async def rotatory_actuator_post(request, device_id: int, actuator_id: int):
    actuator = get_rotatory_actuator(device_id, actuator_id)
    body = request.json
    speed = float(body["speed"])
    clockwise = bool(body("clockwise"))
    try:
        await actuator.command(speed, clockwise)
        return text("OK")
    except ButtplugError as error:
        raise ServerError(f"{error}")

#######################
# Rendering

def render_device(device: Device): 
    device_resource = {
        "@type": "Device",
        "@id": app.url_for('device_get', device_id=device.index),
        "name": device.name,
        "sensors": [app.url_for('sensor_get', device_id=device.index, sensor_id=s.index) for s in device.sensors],
        "actuators": [app.url_for('actuator_get', device_id=device.index, actuator_id=la.index) for la in device.actuators],
        "linear_actuators": [app.url_for('linear_actuator_get', device_id=device.index, actuator_id=la.index) for la in device.linear_actuators],
        "rotatory_actuators": [app.url_for('rotatory_actuator_get', device_id=device.index, actuator_id=ra.index) for ra in device.rotatory_actuators],
    }
    return device_resource

def render_sensor(device_id, sensor: Sensor):
    sensor_resource = {
        "@type": "Sensor",
        "@id": app.url_for('sensor_get', device_id=device_id, sensor_id=sensor.index),
        "description": sensor.description,
        "sensor_reading": app.url_for('sensor_reading_get', device_id=device_id, sensor_id=sensor.index)
    }
    return sensor_resource

async def render_sensor_reading(device_id, sensor: Sensor):
    reading = await sensor.read()
    resource = {
        "@type": "SensorReading",
        "@id": app.url_for('sensor_reading_get', device_id=device_id, sensor_id=sensor.index),
        "sensor_reading_value": [reading]
    }
    return resource

def render_actuator(device_id, actuator: Actuator):
    actuator_resource = {
        "@type": "Actuator",
        "@id": app.url_for('actuator_get', device_id=device_id, actuator_id=actuator.index),
        "description": actuator.description,
        "step_count": actuator.step_count,
    }
    return actuator_resource

#######################
# Commands

# sanic server:app exec rotary --device=0 --actuator=0 --intensity=0.5 --duration=10
@app.command(name="rotary")
async def rotary(device: int, actuator: int, intensity: int, duration: int = 1):
    logger.debug(f"rotary: device={device} device={actuator} device={intensity} duration={duration}")

    # exec does not call before_server_start
    await before_server_start(app)
    client: Client = app.ctx.client
    if not client.connected:
        raise ConnectionError

    device_index = int(device)
    selected_device = client.devices[device_index]
    logger.debug(f"rotary: selected_device={selected_device}")

    actuator_index = int(actuator)
    selected_actuator = selected_device.actuators[actuator_index]
    logger.debug(f"rotary: selected_actuator={selected_actuator}")

    selected_intensity = float(intensity)
    await selected_actuator.command(selected_intensity)

    selected_duration = int(duration)
    logger.debug(f"rotary: selected_duration={selected_duration} seconds")

    await asyncio.sleep(selected_duration)

    # exec does not call after_server_stop
    await after_server_stop(app)

#######################
# Utility methods

def get_client() -> Client:
    client = app.ctx.client
    if not client.connected:
        raise ButtPlugConnectionError
    return client

def get_device(device_id) -> Device:
    client: Client = get_client()
    if len(client.devices) <= device_id:
        raise BadRequest(f"Invalid Device ID {device_id}")
    return client.devices[device_id]

def get_sensor(device_id, sensor_id) -> Sensor:
    device = get_device(device_id)
    if len(device.sensors) <= sensor_id:
        raise BadRequest(f"Invalid Sensor ID {sensor_id}")
    return device.sensors[sensor_id]

def get_actuator(device_id, actuator_id) -> Actuator:
    device = get_device(device_id)
    if len(device.actuators) <= actuator_id:
        raise BadRequest(f"Invalid Actuator ID {actuator_id}")
    return device.actuators[actuator_id]

def get_linear_actuator(device_id, actuator_id) -> Actuator:
    device = get_device(device_id)
    if len(device.linear_actuators) <= actuator_id:
        raise BadRequest(f"Invalid Linear Actuator ID {actuator_id}")
    return device.linear_actuators[actuator_id]

def get_rotatory_actuator(device_id, actuator_id) -> Actuator:
    device = get_device(device_id)
    if len(device.rotatory_actuators) <= actuator_id:
        raise BadRequest(f"Invalid Rotatory Actuator ID {actuator_id}")
    return device.rotatory_actuators[actuator_id]

if __name__ == '__main__':
    app.run()