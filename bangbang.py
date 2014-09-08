#!/usr/bin/env python
from datetime import datetime
from time import time
from random import randint

# TBD: proper logging (via python logging)
# TBD: add multi-controller test
# TBD: setting high/low values
#      (should be done regardless of state with on_value() call afterwards;
#      homA UI needs some improvements)
# TBD: charts / data logs


class State(object):
    def __init__(self, controller):
        self.controller = controller
        self.client = self.controller.client

    def enter(self):
        pass

    def on_connect(self):
        pass

    def retained_enable(self, enable):
        pass

    def disable(self):
        pass

    def enable(self):
        pass

    def on_value(self, value):
        pass

    def on_ready(self):
        pass


class DisconnectedState(State):
    def on_connect(self):
        self.controller.set_state(NotReadyState)


class NotReadyState(State):
    def enter(self):
        self.client.subscribe(self.controller.enabled_topic)
        self.client.subscribe(self.controller.enabled_on_topic)
        self.client.subscribe(self.controller.measurement_topic)

    def retained_enable(self, enable):
        print "retained_enable(%r)" % enable
        self.controller.enabled = enable

    def on_ready(self):
        self.client.unsubscribe(self.controller.enabled_topic)
        self.controller.publish_metadata()
        if self.controller.enabled:
            self.controller.set_state(ActivationState)
        else:
            self.controller.set_state(DisabledState)


class DisabledState(State):
    def enter(self):
        self.controller.toggle_relay(False)

    def enable(self):
        print "%s: enabling" % self.controller.name
        self.controller.publish(self.controller.enabled_topic, "1")
        self.controller.set_state(ActivationState)


class EnabledState(State):
    def disable(self):
        print "%s: disabling" % self.controller.name
        self.controller.publish(self.controller.enabled_topic, "0")
        self.controller.set_state(DisabledState)


# Note that we don't toggle relays before state change
# as new state's on_value() will be called immediatelly
# after state change
class ActivationState(EnabledState):
    def on_value(self, value):
        # don't toggle relay here, it will be toggled
        # by OffState.on_value() or OnState.value() immediatelly after
        # state change
        print "ActivationState.on_value(%s)" % value
        if value < self.controller.high:
            self.controller.set_state(OnState)
        else:
            self.controller.set_state(OffState)


class OffState(EnabledState):
    def on_value(self, value):
        if value < self.controller.low:
            self.controller.set_state(OnState)
        else:
            self.controller.toggle_relay(False)


class OnState(EnabledState):
    def on_value(self, value):
        if value >= self.controller.high:
            self.controller.set_state(OffState)
        else:
            self.controller.toggle_relay(True)


class BangBangController(object):
    def __init__(self, client, name, measurement_topic, relay_topic,
                 low, high, enabled=False):
        self.client = client
        self.name = name
        self.measurement_topic = measurement_topic
        self.relay_topic = relay_topic
        self.low = low
        self.high = high
        self.enabled = bool(enabled)
        self.active = True
        self.value = None
        self._states = {}
        self.enabled_topic = "/devices/%s/controls/Enabled" % self.name
        self.enabled_on_topic = self.enabled_topic + "/on"
        self.set_state(DisconnectedState)

    def publish_metadata(self):
        self.publish("/devices/%s/meta/name" % self.name, self.name)
        self.publish("%s/meta/type" % self.enabled_topic, "switch")
        self.publish("%s/meta/order" % self.enabled_topic, "1")
        self.publish(self.enabled_topic, "1" if self.enabled else "0")

    def set_state(self, state_cls):
        old_state = self.state.__class__.__name__ if hasattr(self, "state") else "(start)"
        if state_cls not in self._states:
            self._states[state_cls] = state_cls(self)
        print "%s: %s --> %s (value %r)" % \
            (self.name, old_state, state_cls.__name__, self.value)
        self.state = self._states[state_cls]
        self.state.enter()
        self.set_enabled()
        self.handle_value()

    def set_enabled(self, enabled=None):
        if enabled is not None:
            self.enabled = bool(enabled)
        if self.enabled:
            self.state.enable()
        else:
            self.state.disable()

    def handle_value(self, value=None):
        print "handle_value %r" % value
        if value is not None:
            self.value = value
        if self.value is not None:
            self.state.on_value(self.value)

    def on_connect(self):
        self.state.on_connect()

    def on_ready(self):
        self.state.on_ready()

    def on_message(self, msg):
        if msg.topic == self.enabled_on_topic and msg.payload:
            self.set_enabled(msg.payload != "0")
        elif msg.topic == self.enabled_topic:
            self.state.retained_enable(msg.payload != "0")
        elif msg.topic == self.measurement_topic and msg.payload:
            value = float(msg.payload)
            dt = datetime.now().isoformat(" ")
            print "%s %s: %s, value %r" % (dt, self.name, self.state.__class__.__name__, value)
            self.handle_value(value)

    def toggle_relay(self, on):
        print "%s: %s" % (self.name, "on" if on else "off")
        self.publish(self.relay_topic, "1" if on else "0", retain=False)

    def publish(self, topic, payload, retain=True):
        self.client.publish(topic, payload, 0, retain)


class BangBangHandler(object):
    def __init__(self, client, host="localhost", port=1883, marker_topic=None):
        self.client = client
        self.host = host
        self.port = port
        self._marker_topic = marker_topic or \
            "/tmp/%s/retain_hack" % (str(time()) + str(randint(0, 100000)))
        self.controllers = []
        self.ready = False
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def add_controller(self, *args, **kwargs):
        self.controllers.append(BangBangController(self.client, *args, **kwargs))

    def connect(self):
        self.client.connect(self.host, self.port)

    def on_connect(self, client, userdata, rc):
        if rc != 0:
            return
        # let controllers make their subscriptions before publishing
        # the marker topic, so retained messages arrive while controllers
        # are in their NotReadyState
        for controller in self.controllers:
            controller.on_connect()
        self.client.subscribe(self._marker_topic)
        self.client.publish(self._marker_topic, "1")

    def on_message(self, client, userdata, msg):
        print "on_message: %r %r" % (msg.topic, msg.payload)
        if msg.topic == self._marker_topic:
            self.ready = True
            for controller in self.controllers:
                controller.on_ready()
        else:
            for controller in self.controllers:
                controller.on_message(msg)

import click
import mosquitto


@click.command()
@click.option("-h", "--host", default="localhost", help="MQTT host")
@click.option("-p", "--port", default=1883, help="MQTT host")
@click.option("--water-low", default=440, help="Low water level")
@click.option("--water-high", default=590, help="High water level")
@click.option("--room-temp-low", default=22, help="Low room temp level")
@click.option("--room-temp-high", default=23, help="High room temp level")
def bangbang(host, port, water_low, water_high, room_temp_low, room_temp_high):
    client = mosquitto.Mosquitto("bangbang")
    handler = BangBangHandler(client, host="192.168.20.22")

    handler.add_controller(
        "water",
        measurement_topic="/devices/wb-adc/controls/ADC4",
        relay_topic="/devices/drb88/controls/Relay 6/on",
        low=water_low, high=water_high, enabled=True)

    handler.add_controller(
        "roomtemp",
        measurement_topic="/devices/msu34tlp/controls/Temp 1",
        relay_topic="/devices/drb88/controls/Relay 8/on",
        low=room_temp_low, high=room_temp_high)

    handler.connect()

    while True:
        rc = client.loop()
        if rc != 0:
            break

if __name__ == "__main__":
    bangbang()
