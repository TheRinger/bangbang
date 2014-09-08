import unittest
from nose.tools import assert_multi_line_equal
from collections import deque
from mosquitto import topic_matches_sub, MosquittoMessage
from bangbang import BangBangHandler

assert_multi_line_equal.im_class.maxDiff = None


class FakeMosquittoClient(object):
    def __init__(self, userdata=None):
        self._userdata = userdata
        self.on_connect = lambda client, userdata, rc: None
        self.on_message = lambda client, userdata, rc: None
        self.connected = False
        self._subscriptions = []
        self._queue = deque()
        self.message_log = []

    def connect(self, host, port=1883):
        assert not self.connected, "Already connected"
        assert not hasattr(self, "connected_host"), "Already connecting"
        self.connected_host = host
        self.connected_port = port

    def fake_finish_connecting(self):
        assert hasattr(self, "connected_host"), "Not connecting"
        self.connected = True
        self.on_connect(self, self._userdata, 0)

    def publish(self, topic, payload=None, qos=0, retain=False,
                allow_disconnected=False):
        assert self.connected or allow_disconnected, "Not connected"
        message = MosquittoMessage()
        message.topic = topic
        message.payload = payload
        message.qos = qos
        message.retain = retain
        self.message_log.append((topic, payload, qos, retain))
        self._queue.append(message)

    def _deliver_message(self, message):
        for sub in self._subscriptions:
            if topic_matches_sub(sub, message.topic):
                self.on_message(self, self._userdata, message)

    def deliver(self):
        assert self.connected, "Not connected"
        while self._queue:
            self._deliver_message(self._queue.popleft())

    def subscribe(self, topic, qos=0):
        assert self.connected, "Not connected"
        self._subscriptions.append(topic)

    def unsubscribe(self, topic):
        self._subscriptions.remove(topic)

    def verify_log(self, *expected_message_log):
        assert_multi_line_equal(
            "\n".join(map(repr, expected_message_log)),
            "\n".join(map(repr, self.message_log)))
        self.clear_log()

    def clear_log(self):
        self.message_log = []


class BangBangTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeMosquittoClient()
        self.bang_bang_handler = BangBangHandler(
            client=self.client,
            host="somehost.example.com",
            port=18830,
            marker_topic="/tmp/zzz/retain_hack")
        self.bang_bang_handler.add_controller(
            "watertank", low=440, high=520,
            measurement_topic="/devices/wb-adc/controls/ADC4",
            relay_topic="/devices/drb88/controls/Relay 6/on",
            enabled=True)

    def connect(self):
        self.bang_bang_handler.connect()
        self.client.fake_finish_connecting()
        self.assertTrue(self.client.connected)
        self.assertEqual("somehost.example.com", self.client.connected_host)
        self.assertEqual(18830, self.client.connected_port)

    def publish(self, topic, payload, retain=True, allow_disconnected=False):
        self.client.publish(topic, payload, 0, retain,
                            allow_disconnected=allow_disconnected)

    def test_connect(self):
        self.connect()
        self.client.verify_log(("/tmp/zzz/retain_hack", "1", 0, False))
        self.assertFalse(self.bang_bang_handler.ready)
        self.client.deliver()
        self.assertTrue(self.bang_bang_handler.ready)
        self.client.verify_log(("/devices/watertank/meta/name", "watertank", 0, True),
                               ("/devices/watertank/controls/Enabled/meta/type", "switch", 0, True),
                               ("/devices/watertank/controls/Enabled/meta/order", "1", 0, True),
                               ("/devices/watertank/controls/Enabled", "1", 0, True))

    def connect_and_clear_log(self):
        self.connect()
        self.client.deliver()
        self.client.clear_log()

    def test_controller(self):
        self.connect_and_clear_log()

        # activation: pump is initially on
        self.publish("/devices/wb-adc/controls/ADC4", "460")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "460", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "1", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "500")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "500", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "1", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "530")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "530", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "0", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "490")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "490", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "0", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "430")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "430", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "1", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "530")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "530", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "0", 0, False))

    def test_activation_with_high_level(self):
        self.connect_and_clear_log()

        # activation: pump is initially off
        self.publish("/devices/wb-adc/controls/ADC4", "560")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "560", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "0", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "430")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "430", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "1", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "530")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "530", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "0", 0, False))

    def test_disabling_enabling(self):
        self.connect_and_clear_log()

        self.publish("/devices/watertank/controls/Enabled/on", "0", retain=False)
        self.client.deliver()
        self.client.verify_log(("/devices/watertank/controls/Enabled/on", "0", 0, False),
                               ("/devices/watertank/controls/Enabled", "0", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "0", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "430")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "430", 0, True))

        self.publish("/devices/wb-adc/controls/ADC4", "100")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "100", 0, True))

        # NOTE: when low/high values change, on_value() should be called, too.
        self.publish("/devices/watertank/controls/Enabled/on", "1", retain=False)
        self.client.deliver()
        self.client.verify_log(("/devices/watertank/controls/Enabled/on", "1", 0, False),
                               ("/devices/watertank/controls/Enabled", "1", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "1", 0, False))

    def test_retained_disabling(self):
        self.publish("/devices/watertank/controls/Enabled", "0", allow_disconnected=True)
        self.connect()
        self.client.deliver()
        self.client.verify_log(('/devices/watertank/controls/Enabled', '0', 0, True),
                               ('/tmp/zzz/retain_hack', '1', 0, False),
                               ("/devices/watertank/meta/name", "watertank", 0, True),
                               ("/devices/watertank/controls/Enabled/meta/type", "switch", 0, True),
                               ("/devices/watertank/controls/Enabled/meta/order", "1", 0, True),
                               ("/devices/watertank/controls/Enabled", "0", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "0", 0, False))

        self.publish("/devices/wb-adc/controls/ADC4", "100")
        self.client.deliver()
        self.client.verify_log(("/devices/wb-adc/controls/ADC4", "100", 0, True))

        self.publish("/devices/watertank/controls/Enabled/on", "1", retain=False)
        self.client.deliver()
        self.client.verify_log(("/devices/watertank/controls/Enabled/on", "1", 0, False),
                               ("/devices/watertank/controls/Enabled", "1", 0, True),
                               ("/devices/drb88/controls/Relay 6/on", "1", 0, False))
