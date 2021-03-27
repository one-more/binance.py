from abc import ABC, abstractmethod
from typing import Dict
from aiohttp import ClientWebSocketResponse
from termcolor import colored
from datetime import datetime
from . import __version__
import aiohttp
import asyncio
import logging
import json


class EventsDataStream(ABC):
    last_msg_time: float

    def __init__(self, client, endpoint, user_agent):
        self.client = client
        self.endpoint = endpoint
        if user_agent:
            self.user_agent = user_agent
        else:
            self.user_agent = f"binance.py (https://git.io/binance.py, {__version__})"

    async def _handle_messages(self, web_socket):
        while True:
            msg = await web_socket.receive()
            self.last_msg_time = datetime.now().timestamp()
            if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                logging.error(
                    "Trying to receive something while the websocket is closed! Trying to reconnect."
                )
                asyncio.ensure_future(self.connect())
            elif msg.type is aiohttp.WSMsgType.ERROR:
                logging.error(
                    f"Something went wrong with the websocket, reconnecting..."
                )
                asyncio.ensure_future(self.connect())
            self._handle_event(json.loads(msg.data))

    @abstractmethod
    def _handle_event(self, event_data: Dict):
        pass

    @abstractmethod
    def connect(self):
        pass


class MarketEventsDataStream(EventsDataStream):
    web_socket: ClientWebSocketResponse

    def __init__(self, client, endpoint, user_agent):
        super().__init__(client, endpoint, user_agent)

    async def start(self):
        async with aiohttp.ClientSession() as session:
            combined_streams = "/".join(self.client.events.registered_streams)
            if self.client.proxy:
                self.web_socket = await session.ws_connect(
                    f"{self.endpoint}/stream?streams={combined_streams}",
                    proxy=self.client.proxy,
                )
            else:
                self.web_socket = await session.ws_connect(
                    f"{self.endpoint}/stream?streams={combined_streams}"
                )
            await self._handle_messages(self.web_socket)

    def _handle_event(self, content):
        if "stream" in content:
            stream_name = content["stream"]
            content = content["data"]

            if isinstance(content, list):
                for event_content in content:
                    event_content["stream"] = stream_name
                    self.client.events.wrap_event(event_content).fire()
            else:
                content["stream"] = stream_name
                self.client.events.wrap_event(content).fire()

        else:
            print(
                colored(
                    "received event without stream: {}".format(content),
                    "cyan"
                )
            )

    def connect(self):
        self.start()

    def connected(self):
        if len(self.client.events.registered_streams):
            now = datetime.now()
            delta = now - datetime.fromtimestamp(self.last_msg_time)
            if delta.seconds > 2:
                return False

            return True

        return True

    def close(self):
        self.web_socket.close()


class UserEventsDataStream(EventsDataStream):
    web_socket: aiohttp.ClientWebSocketResponse

    def __init__(self, client, endpoint, user_agent):
        super().__init__(client, endpoint, user_agent)

    async def _heartbeat(
        self, listen_key, interval=60 * 30
    ):  # 30 minutes is recommended according to
        # https://github.com/binance-exchange/binance-official-api-docs/blob/master/user-data-stream.md#pingkeep-alive-a-listenkey
        while True:
            await asyncio.sleep(interval)
            await self.client.keep_alive_listen_key(listen_key)

    async def start(self):
        async with aiohttp.ClientSession() as session:
            listen_key = (await self.client.create_listen_key())["listenKey"]
            if self.client.proxy:
                web_socket = await session.ws_connect(
                    f"{self.endpoint}/ws/{listen_key}"
                )
            else:
                web_socket = await session.ws_connect(
                    f"{self.endpoint}/ws/{listen_key}", proxy=self.client.proxy
                )
            self.web_socket = web_socket
            asyncio.ensure_future(self._heartbeat(listen_key))
            await self._handle_messages(web_socket)

    def _handle_event(self, content):
        event = self.client.events.wrap_event(content)
        event.fire()

    def connect(self):
        self.start()

    def connected(self):
        return not self.web_socket.closed
