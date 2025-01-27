import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, List, Union

import ujson
from buttplug import ButtplugError, Client, Device, ProtocolSpec, WebsocketConnector
from buttplug.client import Actuator, Sensor
from pydantic import AnyUrl, BaseModel, Field, RootModel, field_serializer
from pydantic import ValidationError as PydanticValidationError
from sanic import NotFound, Sanic, SanicException, ServerError
from sanic.log import logger
from sanic.response import json as sanic_json
from sanic_ext import openapi, validate
from sanic_ext.exceptions import ValidationError as SanicExtValidationError

Number = Union[int, float]


class DeviceNotFound(NotFound):
    def __init__(self, device_id: int) -> None:
        self.message = "Device Not Found"
        self.device_id = device_id


class ActuatorNotFound(NotFound):
    def __init__(self, actuator_id: int) -> None:
        self.message = "Actuator Not Found"
        self.actuator_id = actuator_id


class SensorNotFound(NotFound):
    def __init__(self, sensor_id: int) -> None:
        self.message = "Sensor Not Found"
        self.sensor_id = sensor_id


@openapi.component
class ActuatorCommand(BaseModel):
    intensity: float = Field(
        ge=0.0, le=1.0, examples=list("1.0"), description="Intensity (0.0-1.0)"
    )


@openapi.component
class LinearActuatorCommand(BaseModel):
    duration: int = Field(
        ge=0.0, examples=list("5000"), description="Time duration in milliseconds"
    )
    position: float = Field(
        ge=0.0,
        le=1.0,
        examples=list("1.0"),
        description="Position in linear axis (0.0 - 1.0)",
    )


@openapi.component
class RotatoryActuatorCommand(BaseModel):
    speed: float = Field(
        ge=0.0, le=1.0, examples=list("1.0"), description="Rotation speed (0.0 - 1.0)"
    )
    clockwise: bool = Field(
        default=False,
        examples=list("true"),
        description="True if rotating clockwise, otherwise false.",
    )


class BaseItem(BaseModel):
    id: str = Field(alias="@id")
    type: str = Field(..., alias="@type")

    class Config:
        populate_by_name = True
        ser_json_by_alias = True
        json_encoders = {AnyUrl: str}


@openapi.component
class DeviceItem(BaseItem):
    type: str = Field("Device", alias="@type", examples=list("Device"))
    name: str = Field(examples=list("My Device"))
    sensors: List[str] = Field(default=[], examples=list("[/devices/0/sensors/0]"))
    actuators: List[str] = Field(default=[], examples=list("[/devices/0/actuators/0]"))
    linear_actuators: List[str] = Field(
        default=[], examples=list("[/devices/0/linear_actuators/0]")
    )
    rotatory_actuators: List[str] = Field(
        default=[], examples=list("[/devices/0/rotatory_actuators/0]")
    )


class DeviceItemList(RootModel):
    root: list[DeviceItem]


@openapi.component
class SensorItem(BaseItem):
    type: str = Field("Sensor", alias="@type", examples=list("Sensor"))
    description: str = Field(examples=list("Sensor Description"))
    sensor_reading: str = Field(examples=list("/devices/0/sensors/0/read"))


class SensorItemList(RootModel):
    root: list[SensorItem]


@openapi.component
class SensorReadingItem(BaseItem):
    type: str = Field("SensorReading", alias="@type", examples=list("Sensor"))
    instant: datetime = Field(
        examples=list("Sensor"), description="Reading instant at UTC"
    )
    value: List[Number] = Field(
        alias="@value", description="Sensor readings (ints or floats)"
    )

    @field_serializer("instant")
    def serialize_dt(self, dt: datetime, _info):
        return dt.isoformat()


@openapi.component
class ActuatorItem(BaseItem):
    type: str = Field("Actuator", alias="@type", examples=list("Actuator"))
    description: str = Field(examples=list("Actuator Description"))
    step_count: int = Field(examples=list("69"))


class ActuatorItemList(RootModel):
    root: list[ActuatorItem]


@openapi.component
class LinearActuatorItem(ActuatorItem):
    type: str = Field("LinearActuator", alias="@type", examples=list("LinearActuator"))


class LinearActuatorItemList(RootModel):
    root: list[LinearActuatorItem]


@openapi.component
class RotatoryActuatorItem(ActuatorItem):
    type: str = Field(
        "RotatoryActuator", alias="@type", examples=list("RotatoryActuator")
    )


class RotatoryActuatorItemList(RootModel):
    root: list[RotatoryActuatorItem]


class ButtPlugConnectionError(SanicException):
    status_code = 502
    message = "Client Connection Failed"


def pydantic_serializer(obj):
    if isinstance(obj, BaseModel):
        return obj.model_dump(by_alias=True)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

my_log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        # Console handlers with colors
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "colored",
            "stream": "ext://sys.stdout",
        },
        # File handlers without colors
        "access_file": {
            "class": "logging.FileHandler",
            "filename": "access.log",
            "formatter": "plain_access",
        },
        "error_file": {
            "class": "logging.FileHandler",
            "filename": "error.log",
            "formatter": "plain_generic",
        },
        "root_file": {
            "class": "logging.FileHandler",
            "filename": "internal.log",
            "formatter": "plain_generic",
        }
    },
    "formatters": {
        # Colored console formats
        "colored": {
            "class": "sanic.logging.formatter.AutoFormatter",
            "colorize": True,
            "format": "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "colored_access": {
            "class": "sanic.logging.formatter.AutoAccessFormatter",
            "colorize": True,
            "format": "%(asctime)s - (%(name)s)[%(levelname)s][%(host)s]: %(request)s %(message)s %(status)d %(byte)d",
        },
        # Plain file formats (no ANSI)
        "plain_generic": {
            "format": "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "class": "logging.Formatter",
        },
        "plain_access": {
            "format": "%(asctime)s - (%(name)s)[%(levelname)s][%(host)s]: %(request)s %(message)s %(status)d %(byte)d",
            "datefmt": "%Y-%m-%d %H:%M:%S",
            "class": "logging.Formatter",
        }
    },
    "loggers": {
        "sanic.root": {
            "level": "INFO",
            "handlers": ["console", "root_file"],
        },
        "sanic.error": {
            "level": "ERROR",
            "handlers": ["console", "error_file"],
        },
        "sanic.access": {
            "level": "INFO",
            "handlers": ["console", "access_file"],
            "propagate": False,  # Disable default Sanic access logging
        }
    }
}

app = Sanic(
    "ButtRest",
    env_prefix="BUTTREST_",
    dumps=lambda obj: ujson.dumps(obj, default=pydantic_serializer),
    log_config=my_log_config
)
app.config.CLIENT_NAME = "ButtRest"
app.config.FALLBACK_ERROR_FORMAT = "json"

@app.exception(SanicExtValidationError)
async def handle_validation_error(request, exception: SanicExtValidationError):
    status = 422
    cause = exception.extra["exception"]
    if isinstance(cause, PydanticValidationError):
        # Extract Pydantic error details
        errors = cause.errors()
        error_details = []
        for error in errors:
            error_details.append(
                {
                    "pointer": f"/{list(error['loc'])[0]}",
                    "ctx": error.get("ctx"),
                    "code": error["type"],
                    "detail": error["msg"],
                }
            )

        # RFC 9457 Problem Details format
        problem_details = {
            "type": "https://problems-registry.smartbear.com/validation-error",
            "title": "Validation Error",
            "status": status,
            "detail": "Request validation failed",
            "errors": error_details,
        }
        return sanic_json(
            problem_details, status=status, content_type="application/problem+json"
        )
    else:
        # Fallback for other SanicExt ValidationErrors
        return sanic_json(
            {
                "type": "https://problems-registry.smartbear.com/validation-error",
                "title": "Validation Error",
                "status": status,
                "detail": str(exception),
            },
            status=status,
            content_type="application/problem+json",
        )


@app.exception(SanicException)
async def handle_exception(request, exception: SanicException):
    status = exception.status_code
    return sanic_json(
        {"title": exception.message, "status": status, "detail": str(exception)},
        status=status,
        content_type="application/problem+json",
    )


def jsonld(body: Any, status: int = 200):
    return sanic_json(body=body, status=status, content_type="application/ld+json")


#######################
# Server Lifecycle


@app.before_server_start
async def before_server_start(app):
    # export BUTTREST_CLIENT_NAME=myclientname
    client = Client(app.config.CLIENT_NAME, ProtocolSpec.v3)
    if app.debug:
        logger.info("Setting client logger to DEBUG level")
        client.logger.setLevel(level=logging.DEBUG)

    # export BUTTREST_INTIFACE_URL=ws://localhost:12345
    connector = WebsocketConnector(app.config.INTIFACE_URL, logger=client.logger)

    # Make buttplug logging level match sanic logging level
    client.logger.level = logger.level

    await client.connect(connector)
    await client.start_scanning()
    await asyncio.sleep(3)
    await client.stop_scanning()

    logger.info(f"Registered devices: {client.devices}")
    app.ctx.client = client


@app.after_server_stop
async def after_server_stop(app):
    await app.ctx.client.disconnect()


#######################
# Handlers


@app.get("/healthz")
async def health_check(request):
    return jsonld({"status": "ok"})


@app.get("/")
async def index(request):
    return jsonld({"status": "ok"})


@app.post("/scan")
@openapi.summary("Scan for devices")
@openapi.description("Calls start_scanning, sleeps 3 seconds, stops scanning")
async def scan(request):
    # this could return 202 and do it in the background
    client: Client = get_client()
    await client.start_scanning()
    await asyncio.sleep(3)
    await client.stop_scanning()
    return jsonld({"status": "ok"})


@app.get("/devices")
@openapi.summary("Get all registered devices")
@openapi.description("Renders devices as array of JSON objects")
@openapi.definition(
    response={
        "application/ld+json": DeviceItemList.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def devices_get(request):
    client: Client = get_client()
    devices = [render_device(device) for device in client.devices.values()]
    return jsonld(DeviceItemList(root=devices))


@app.get("/devices/<device_id:int>")
@openapi.summary("Get a device")
@openapi.description("Renders device as JSON object")
@openapi.definition(
    response={
        "application/ld+json": DeviceItem.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def device_get(request, device_id: int):
    device = get_device(device_id)
    device_resource = render_device(device)
    return jsonld(device_resource)


@app.get("/devices/<device_id:int>/sensors")
@openapi.summary("Get all sensors of device")
@openapi.description("Renders sensors as array of JSON objects")
@openapi.definition(
    response={
        "application/ld+json": SensorItemList.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def sensors_get(request, device_id: int):
    device = get_device(device_id)
    sensor_resources = [render_sensor(device_id, s) for s in device.sensors]
    return jsonld(sensor_resources)


@app.get("/devices/<device_id:int>/sensors/<sensor_id:int>")
@openapi.summary("Get sensor of device")
@openapi.description("Renders sensor as JSON object")
@openapi.definition(
    response={
        "application/ld+json": SensorItem.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def sensor_get(request, device_id: int, sensor_id: int):
    sensor = get_sensor(device_id, sensor_id)
    sensor_resource = render_sensor(device_id, sensor)
    return jsonld(sensor_resource)


@app.get("/devices/<device_id:int>/sensors/<sensor_id:int>/read")
@openapi.summary("Get sensor reading of device")
@openapi.description("Renders sensor reading")
@openapi.definition(
    response={
        "application/ld+json": SensorReadingItem.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def sensor_reading_get(request, device_id: int, sensor_id: int):
    try:
        sensor = get_sensor(device_id, sensor_id)
        # sensor.read doesn't enforce a timeout so we do it here
        readings = await asyncio.wait_for(sensor.read(), timeout=1.0)
        sensor_reading = render_sensor_reading(device_id, sensor, readings)
        return jsonld(sensor_reading)
    except TimeoutError:
        return ServerError(status_code=504, message="Sensor read timed out")


@app.get("/devices/<device_id:int>/actuators")
@openapi.summary("Get actuators of device")
@openapi.description("Renders actuators as array of JSON objects")
@openapi.definition(
    response={
        "application/ld+json": ActuatorItemList.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def actuators_get(request, device_id: int):
    device = get_device(device_id)
    actuator_resources = [render_actuator(device_id, a) for a in device.actuators]
    return jsonld(actuator_resources)


@app.get("/devices/<device_id:int>/actuators/<actuator_id:int>")
@openapi.summary("Get actuator of device")
@openapi.description("Renders actuator as JSON object")
@openapi.definition(
    response={
        "application/ld+json": ActuatorItem.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def actuator_get(request, device_id: int, actuator_id: int):
    actuator = get_actuator(device_id, actuator_id)
    actuator_resource = render_actuator(device_id, actuator)
    return jsonld(actuator_resource)


@app.post("/devices/<device_id:int>/actuators/<actuator_id:int>")
@openapi.summary("Change actuator state")
@openapi.description("Takes json body and sends actuator a command")
@openapi.definition(
    body={
        "application/json": ActuatorCommand.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
@validate(json=ActuatorCommand)
async def actuator_post(
    request, device_id: int, actuator_id: int, body: ActuatorCommand
):
    actuator = get_actuator(device_id, actuator_id)
    intensity = body.intensity
    logger.debug(f"actuator_post: {intensity}")
    try:
        await actuator.command(intensity)
        return jsonld({"status": "ok"})
    except ButtplugError as error:
        raise ServerError(f"{error}")


@app.get("/devices/<device_id:int>/linear_actuators")
@openapi.summary("Get linear actuators of device")
@openapi.description("Renders linear actuators as array of JSON objects")
@openapi.definition(
    response={
        "application/json": LinearActuatorItemList.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def linear_actuators_get(request, device_id: int):
    device = get_device(device_id)
    actuator_resources = [
        render_actuator(device_id, a) for a in device.linear_actuators
    ]
    return jsonld(actuator_resources)


@app.get("/devices/<device_id:int>/linear_actuators/<actuator_id:int>")
@openapi.summary("Get linear actuator of device")
@openapi.description("Renders linear actuator as JSON object")
@openapi.definition(
    response={
        "application/json": LinearActuatorItem.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def linear_actuator_get(request, device_id: int, actuator_id: int):
    actuator = get_linear_actuator(device_id, actuator_id)
    actuator_resource = render_actuator(device_id, actuator)
    return jsonld(actuator_resource)


@app.post("/devices/<device_id:int>/linear_actuators/<actuator_id:int>")
@openapi.summary("Change linear actuator state")
@openapi.description("Takes json body and sends linear actuator a command")
@openapi.definition(
    body={
        "application/json": LinearActuatorCommand.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
@validate(json=LinearActuatorCommand)
async def linear_actuator_post(
    request, device_id: int, actuator_id: int, body: LinearActuatorCommand
):
    actuator = get_linear_actuator(device_id, actuator_id)
    try:
        await actuator.command(body.duration, body.position)
        return jsonld({"status": "ok"})
    except ButtplugError as error:
        raise ServerError(f"{error}")


@app.get("/devices/<device_id:int>/rotatory_actuators")
@openapi.summary("Get rotatory actuators of device")
@openapi.description("Renders rotatory actuators as array of JSON objects")
@openapi.definition(
    response={
        "application/ld+json": RotatoryActuatorItemList.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def rotatory_actuators_get(request, device_id: int):
    device = get_device(device_id)
    actuator_resources = [
        render_actuator(device_id, a) for a in device.linear_actuators
    ]
    return jsonld(actuator_resources)


@app.get("/devices/<device_id:int>/rotatory_actuators/<actuator_id:int>")
@openapi.summary("Get rotatory actuator of device")
@openapi.description("Renders rotatory actuator as JSON object")
@openapi.definition(
    response={
        "application/ld+json": RotatoryActuatorItem.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
async def rotatory_actuator_get(request, device_id: int, actuator_id: int):
    actuator = get_rotatory_actuator(device_id, actuator_id)
    actuator_resource = render_actuator(device_id, actuator)
    return jsonld(actuator_resource)


@app.post("/devices/<device_id:int>/rotatory_actuators/<actuator_id:int>")
@openapi.summary("Changes rotatory actuator state")
@openapi.description("Takes json body and sends rotatory actuator a command")
@openapi.definition(
    body={
        "application/json": RotatoryActuatorCommand.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
    }
)
@validate(json=RotatoryActuatorCommand)
async def rotatory_actuator_post(
    request, device_id: int, actuator_id: int, body: RotatoryActuatorCommand
):
    actuator = get_rotatory_actuator(device_id, actuator_id)
    try:
        await actuator.command(body.speed, body.clockwise)
        return jsonld({"status": "ok"})
    except ButtplugError as error:
        raise ServerError(f"{error}")


#######################
# Rendering


def render_device(device: Device):
    device_id = app.url_for("device_get", device_id=device.index)
    logger.debug(f"device_id = {device_id}")
    device_item = DeviceItem(
        id=app.url_for("device_get", device_id=device.index),
        name=device.name,
        sensors=[
            app.url_for("sensor_get", device_id=device.index, sensor_id=s.index)
            for s in device.sensors
        ],
        actuators=[
            app.url_for("actuator_get", device_id=device.index, actuator_id=la.index)
            for la in device.actuators
        ],
        linear_actuators=[
            app.url_for(
                "linear_actuator_get", device_id=device.index, actuator_id=la.index
            )
            for la in device.linear_actuators
        ],
        rotatory_actuators=[
            app.url_for(
                "rotatory_actuator_get", device_id=device.index, actuator_id=ra.index
            )
            for ra in device.rotatory_actuators
        ],
    )
    return device_item


def render_sensor(device_id, sensor: Sensor):
    sensor_id = app.url_for("sensor_get", device_id=device_id, sensor_id=sensor.index)
    sensor_item = SensorItem(
        id=sensor_id,
        description=sensor.description,
        sensor_reading=app.url_for(
            "sensor_reading_get", device_id=device_id, sensor_id=sensor.index
        ),
    )
    return sensor_item


def render_sensor_reading(device_id, sensor: Sensor, readings: List[Number]):
    resource = SensorReadingItem(
        id=app.url_for(
            "sensor_reading_get", device_id=device_id, sensor_id=sensor.index
        ),
        instant=datetime.now(timezone.utc),
        value=readings,
    )
    return resource


def render_actuator(device_id, actuator: Actuator):
    actuator_id = app.url_for(
        "actuator_get", device_id=device_id, actuator_id=actuator.index
    )
    actuator_item = ActuatorItem(
        id=actuator_id, description=actuator.description, step_count=actuator.step_count
    )
    return actuator_item


#######################
# Commands


# sanic server:app exec test_command --device=0 --actuator=0 --intensity=0.5 --duration=10
@app.command(name="test_command")
async def test_command(device: int, actuator: int, intensity: int, duration: int = 1):
    logger.debug(
        f"test_command: device={device} device={actuator} device={intensity} duration={duration}"
    )

    # exec does not call before_server_start
    await before_server_start(app)
    client: Client = app.ctx.client
    if not client.connected:
        raise ConnectionError

    device_index = int(device)
    selected_device = client.devices[device_index]
    logger.debug(f"test_command: selected_device={selected_device}")

    actuator_index = int(actuator)
    selected_actuator = selected_device.actuators[actuator_index]
    logger.debug(f"test_command: selected_actuator={selected_actuator}")

    selected_intensity = float(intensity)
    await selected_actuator.command(selected_intensity)

    selected_duration = int(duration)
    logger.debug(f"test_command: selected_duration={selected_duration} seconds")

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
        raise DeviceNotFound(device_id)
    return client.devices[device_id]


def get_sensor(device_id, sensor_id) -> Sensor:
    device = get_device(device_id)
    if len(device.sensors) <= sensor_id:
        raise SensorNotFound(sensor_id)
    return device.sensors[sensor_id]


def get_actuator(device_id, actuator_id) -> Actuator:
    device = get_device(device_id)
    if len(device.actuators) <= actuator_id:
        raise ActuatorNotFound(actuator_id)
    return device.actuators[actuator_id]


def get_linear_actuator(device_id, actuator_id) -> Actuator:
    device = get_device(device_id)
    if len(device.linear_actuators) <= actuator_id:
        raise ActuatorNotFound(actuator_id)
    return device.linear_actuators[actuator_id]


def get_rotatory_actuator(device_id, actuator_id) -> Actuator:
    device = get_device(device_id)
    if len(device.rotatory_actuators) <= actuator_id:
        raise ActuatorNotFound(actuator_id)
    return device.rotatory_actuators[actuator_id]


if __name__ == "__main__":
    app.run()
