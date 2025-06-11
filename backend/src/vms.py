from enum import Enum
import logging
import traceback
import xml.etree.ElementTree as ET
import asyncio
import json
import argparse
import struct
import cv2
import av
import io
import uuid
import datetime
import httpx
from abc import ABC, abstractmethod
from httpx import BasicAuth, DigestAuth
from httpx_ntlm import HttpNtlmAuth
import ssl

import numpy as np

from dateutil import parser
from typing import Optional, Dict, AsyncGenerator, Tuple, Any

from resolver import Resolver
from tqdm import tqdm
import time as t

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(f"/tmp/{__name__}.log")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

TIMEOUT = 10.0

"""
 { "Message": "Time Out of stream, image too old", "Type": "INFO", "Time": "{rawDataContent.FrameTime}"}
 { "Message": "Time Out of stream", "Type": "INFO" }
 { "Message": "End time of stream reached", "Type": "INFO", "Time": "{rawDataContent.FrameTime}"}
 { "Message": "Streaming", "FrameTime": "2025-03-17T06:31:55.8370000Z", "IsKeyFrame": true, "MediaType": "Video", "Format": "H264", "Type": "INFO"}
 { "Message": "End readstream", "Type": "INFO"}
"""

# Only for Genetec Gateway
class MessageType(str, Enum):
    TooOld = "Time Out of stream, image too old"            # en dehors du range possible du vms
    NotFound = "Time Out of stream"                         # pas de donnée dans le recorder
    Streaming = "Streaming"                                 # La donnée suivera sera du binaire à décoder selon le format
    EndOfStream = "End time of stream reached"              # après la dernière frame
    ClosingSocket = "End readstream"                        # fermeture du socket

class VideoDecoder:
    def __init__(self, codec: str = None):
        self.__pts_counter = 0
        self.__codec = None
        self.__codec_format = codec
        self.__output = "bgr24"
    
    def set_codec(self, codec: str):
        self.__codec_format = codec
    
    def set_output(self, output: str):
        self.__output = output
    
    def decode(self, data: bytes):
        try:
            if self.__codec is None:
                container = av.open(io.BytesIO(data), mode='r')
                video_stream = container.streams.video[0]
                guess_codec = video_stream.codec_context.codec.name
                if guess_codec != self.__codec_format:
                    logger.error(f"Codec détecté ({guess_codec}) différent du codec attendu ({self.__codec_format})")
                
                self.__codec = av.CodecContext.create(guess_codec, 'r')
                self.__pts_counter = 0

            packet = av.Packet(data)
            packet.pts = self.__pts_counter
            self.__pts_counter += 1
            frames = self.__codec.decode(packet)

            for frame in frames:
                img = frame.to_ndarray(format=self.__output)
                yield img
        
        except Exception as e:
            logger.error(f"Erreur lors du décodage du paquet vidéo {self.__codec_format}: {e}")
            logger.error(traceback.format_exc())
            self.__codec = None
            self.__pts_counter = 0
    
class CameraClient(ABC):
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None

    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc, tb):
        if self.reader:
            self.reader = None

        if self.writer:
            self.writer.close()
            self.writer = None

    @abstractmethod
    async def get_system_info(self) -> Optional[Dict[str, str]]:
        pass
    
    @abstractmethod
    async def start_live(self, camera_guid: str, only_key_frames: bool = True) -> AsyncGenerator[np.ndarray, None]:
        pass

    @abstractmethod
    async def start_replay(self, camera_guid: str, from_time: datetime.datetime, to_time: datetime.datetime, gap: int = 0, only_key_frames: bool = True) -> AsyncGenerator[Tuple[np.ndarray, str], None]:
        pass

    @staticmethod
    def create(host: str, port: int, username: str, password: str, type: str):
        if not type:
            raise Exception("VMS type is required")
        if not host or not port:
            raise Exception("Host and port are required")

        type = type.lower()
        if type == "milestone":
            if not username or not password:
                raise Exception("Username and password are required")
            return lambda: MilestoneCameraClient(host, port, username, password)
        elif type == "genetec":
            return lambda: GenetecCameraClient(host, port)
        else:
            raise Exception("Unknown VMS type")

class GenetecCameraClient(CameraClient):
    
    async def _send_request(self, xml_content: str):
        content_length = len(xml_content)
        http_request = (
            f"Accept: application/json\r\n"
            f"Content-Length: {content_length}\r\n"
            f"Content-Type: application/xml\r\n"
            "\r\n"
            f"{xml_content}"
        )
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=TIMEOUT
            )
            self.writer.write(http_request.encode("utf-8"))
            await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)

            headers = {}
            while True:
                line = await asyncio.wait_for(self.reader.readline(), timeout=TIMEOUT)
                if not line or line == b"\r\n":
                    break
                try:
                    key, value = line.decode("utf-8").strip().split(": ", 1)
                    headers[key.strip().lower()] = value
                except ValueError as e:
                    logger.error(f"Header invalide: {line} | Exception: {e}")
                    continue

            logger.info(f"Headers: {headers}")
            total_length = int(headers.get("content-length", 0))
            logger.info(f"Longueur du contenu: {total_length}")

            if total_length > 0:
                body = await asyncio.wait_for(self.reader.readexactly(total_length), timeout=TIMEOUT*2)
            else:
                body = b""

            mime = headers.get("content-type", "").lower()
            try:
                if "text/json" in mime or "application/json" in mime:
                    text = body.decode("utf-8")
                    logger.info(text)
                    return json.loads(text)
                elif "text/xml" in mime or "application/xml" in mime:
                    text = body.decode("utf-8")
                    logger.info(text)
                    return ET.fromstring(text)
                else:
                    return body
            except Exception as e:
                logger.error(f"Erreur lors du traitement de la réponse (mimetype: {mime}): {e}")
                raise e
            
        except Exception as e:
            logger.error(f"Erreur lors de la requête: {e}")
            logger.error(traceback.format_exc())
            raise e
        finally:
            if self.writer:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=TIMEOUT)

    async def _stream_request(self, xml_content: str) -> AsyncGenerator:
        content_length = len(xml_content)
        http_request = (
            f"Accept: application/json\r\n"
            f"Content-Length: {content_length}\r\n"
            f"Content-Type: application/xml\r\n"
            "\r\n"
            f"{xml_content}"
        )
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=TIMEOUT
            )
            self.writer.write(http_request.encode("utf-8"))
            await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)

            while True:
 
                headers = {}
                while True:
                    line = await asyncio.wait_for(self.reader.readline(), timeout=TIMEOUT)
                    if not line or line == b"\r\n":
                        break
                    try:
                        key, value = line.decode("utf-8").strip().split(": ", 1)
                        headers[key.strip().lower()] = value
                    except ValueError as e:
                        logger.error(f"Header invalide: {line} | Exception: {e}")
                        continue

                logger.info(f"Headers: {headers}")
                total_length = int(headers.get("content-length", 0))
                logger.info(f"Longueur du contenu: {total_length}")

                if total_length > 0:
                    body = await asyncio.wait_for(self.reader.readexactly(total_length), timeout=TIMEOUT*2)
                else:
                    body = b""

                mime = headers.get("content-type", "").lower()
                try:
                    if "text/json" in mime or "application/json" in mime:
                        text = body.decode("utf-8")
                        logger.info(text)
                        yield json.loads(text)
                    elif "text/xml" in mime or "application/xml" in mime:
                        text = body.decode("utf-8")
                        logger.info(text)
                        yield ET.fromstring(text)
                    else:
                        yield body
                except Exception as e:
                    logger.error(f"Erreur lors du traitement de la réponse (mimetype: {mime}): {e}")
                    raise e
        except Exception as e:
            logger.error(f"Erreur lors de la requête: {e}")
            logger.error(traceback.format_exc())
            raise e
        finally:
            if self.writer:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=TIMEOUT)
  
    async def get_system_info(self) -> Optional[Dict[str, str]]:
        xml = """<?xml version="1.0" encoding="UTF-8"?><methodcall><requestid>0</requestid><methodname>systeminfo</methodname></methodcall>"""
        response = await self._send_request(xml)
        res = {}
        for item in response:
            res[item] = {
                "name": response[item],
            }
        return res

    def _parse_data(self, data: bytes):
        format_str = '>HIHHHQIIIII'
        expected_size = struct.calcsize(format_str)
        bytes = struct.unpack(format_str, data[:expected_size])
        return bytes, data[expected_size:]
    
    async def start_live(self, camera_guid: str, only_key_frames: bool = True) -> AsyncGenerator[np.ndarray, None]:

        xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<methodcall>"
            f"<requestid>1</requestid>"
            f"<methodname>live</methodname>"
            f"<cameraid>{camera_guid}</cameraid>"
            f"</methodcall>"
        )
        stream = self._stream_request(xml)

        _ = await anext(stream)
        async for data in stream:
            headers, frame = self._parse_data(data)

            image_format = headers[2]
            width, height = headers[6], headers[7]
            stride_Y, stride_UV = headers[8], headers[9]

            img = None
            if image_format == 1:   # IF_LUM8
                img_lum = np.frombuffer(frame, dtype=np.uint8).reshape((height, width))
                img = cv2.cvtColor(img_lum, cv2.COLOR_GRAY2BGR)
            elif image_format == 2: # IF_YUV420P
                yuv_frame = np.frombuffer(frame, dtype=np.uint8).reshape((height * 3 // 2, width))
                img = cv2.cvtColor(yuv_frame, cv2.COLOR_YUV2BGR_I420)
            else:
                break

            yield img

    async def start_replay(self, camera_guid: str, from_time: datetime.datetime, to_time: datetime.datetime, gap: int = 0, only_key_frames: bool = True) -> AsyncGenerator[Tuple[np.ndarray, str], None]:
        if from_time > to_time:
            raise ValueError("from_time must be before to_time")
        if from_time > datetime.datetime.now().astimezone(datetime.timezone.utc):
            raise ValueError("from_time must be in the past")
        if to_time > datetime.datetime.now().astimezone(datetime.timezone.utc):
            raise ValueError("to_time must be in the past")
    
        xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<methodcall>"
            f"<requestid>1</requestid>"
            f"<methodname>replay</methodname>"
            f"<cameraid>{camera_guid}</cameraid>"
            f"<fromtime>{from_time.isoformat()}</fromtime>"
            f"<totime>{to_time.isoformat()}</totime>"
            f"<gap>{gap}</gap>"
            f"</methodcall>"
        )
        response = self._stream_request(xml)

        time_frame = None
        codec_format = None
        decoder = VideoDecoder()

        async for data in response:
            if isinstance(data, dict):
                message = data.get("Message", "Streaming")
                logger.info(f"Message: {message} -- {data}")
                if message == MessageType.Streaming:
                    time_str = data.get("FrameTime")
                    time_frame = (
                        parser.isoparse(time_str).astimezone(datetime.timezone.utc)
                        if time_str else None
                    )
                    codec_format = data.get("Format").lower()
                    decoder.set_codec(codec_format)
                elif message in [MessageType.EndOfStream, MessageType.ClosingSocket]:
                    logger.info(f"Message: {message}")
                    break
                elif message in [MessageType.NotFound, MessageType.TooOld]:
                    logger.info(f"Message: {message}")
                else:
                    logger.warning(f"Message inconnu: {message}")
            
            elif isinstance(data, bytes):
                for img in decoder.decode(data):
                    yield img, time_frame
                
class MilestoneCameraClient(CameraClient):

    def __init__(self, host: str, port: int, username: str, password: str, is_fallback: bool = True, auth_method: str = None):
        super().__init__(host, port)
        self.__username = username
        self.__password = password
        self.__protocol = "https" if port == 443 else "http"
        self.__uuid = uuid.uuid4().hex.upper()
        self.__is_fallback = is_fallback
        self.__set_namespace()
        self.__ssl_context = ssl.create_default_context()
        self.__ssl_context.check_hostname = False
        self.__ssl_context.verify_mode = ssl.CERT_NONE

        self.__auth = None
        if auth_method == "ntlm":
            self.__auth = HttpNtlmAuth(self.__username, self.__password)
        elif auth_method == "basic":
            self.__auth = BasicAuth(self.__username, self.__password)
        elif auth_method == "digest":
            self.__auth = DigestAuth(self.__username, self.__password)

        self.__token = None
        self.__token_expiration = None
        self.requestid = 1
        
    async def __aenter__(self):
        if not self.__auth:
            self.__auth = await self.__guess_auth_method()

        await self._login()
        return await super().__aenter__()
    
    async def __aexit__(self, exc_type, exc_value, traceback):
        await self._logout()
        return await super().__aexit__(exc_type, exc_value, traceback)
    
    async def get_system_info(self) -> Optional[Dict[str, str]]:
        if not self.__token:
            raise Exception("Not logged in")
        
        header = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": self.__config
        }
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            f'<soap:Body>'
                f'<GetConfiguration xmlns="{self.__namespace}">'
                    f'<Token>{self.__token}</Token>'
                f'</GetConfiguration>'
            f'</soap:Body>'
            f'</soap:Envelope>'
        )
        
        data = await self._send_request(header, body)

        res = {}
        recorders = data.findall(".//ns:RecorderInfo", {"ns": self.__namespace})
        for recorder in recorders:
            HostName = recorder.find(".//ns:HostName", {"ns": self.__namespace}).text
            WebServerUri = recorder.find(".//ns:WebServerUri", {"ns": self.__namespace}).text

            cameras = recorder.findall(".//ns:CameraInfo", {"ns": self.__namespace})

            for camera in cameras:
                name = camera.find(".//ns:Name", {"ns": self.__namespace}).text
                uuid = camera.find(".//ns:DeviceId", {"ns": self.__namespace}).text
                res[uuid] = {
                    "name": name,
                    "hostName": HostName,
                    "webServerUri": WebServerUri
                }

        return res

    async def _send_request(self, header: str, body: str) -> Optional[str]:
        url = f"{self.__protocol}://{self.host}:{self.port}{self.__endpoint}"
        
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                url,
                headers=header,
                content=body,
                auth=self.__auth
            )
            
            if response.status_code == 200:
                mime = response.headers.get("Content-Type", "").lower()
                if "text/xml" in mime or "application/xml" in mime:
                    data = ET.fromstring(response.text)
                    return data
                else:
                    raise Exception(f"Failed to login: undefined mime type {mime}")
            else:
                raise Exception(f"Failed to login: HTTP {response.status_code}")

    async def _login(self) -> Optional[str]:
        header = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": self.__login
        }
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            f'<soap:Body>'
                f'<Login xmlns="{self.__namespace}">'
                    f'<instanceId>{self.__uuid}</instanceId>'
                    f'<currentToken></currentToken>'
                f'</Login>'
            f'</soap:Body>'
            f'</soap:Envelope>'
        )

        data = await self._send_request(header, body)
        self.__token = data.find(".//ns:Token", {"ns": self.__namespace}).text

        TTL = int(data.find(".//ns:TimeToLive/ns:MicroSeconds", {"ns": self.__namespace}).text)/1000000 - 30
        self.__token_expiration = datetime.datetime.now() + datetime.timedelta(seconds=TTL)

        return self.__token
    
    async def _logout(self):
        header = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": self.__logout
        }
        body = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            f'<soap:Body>'
                f'<Logout xmlns="{self.__namespace}">'
                    f'<instanceId>{self.__uuid}</instanceId>'
                    f'<currentToken>{self.__token}</currentToken>'
                f'</Logout>'
            f'</soap:Body>'
            f'</soap:Envelope>'
        )

        data = await self._send_request(header, body)
        return data

    async def __guess_auth_method(self):
        self.__is_fallback = True
        self.__set_namespace()
        self.__auth = HttpNtlmAuth(self.__username, self.__password)
        try:
            await self._login()
            return self.__auth
        except Exception as e:
            pass

        self.__auth = BasicAuth(self.__username, self.__password)
        try:
            await self._login()
            return self.__auth
        except Exception as e:
            pass

        self.__auth = DigestAuth(self.__username, self.__password)
        try:
            await self._login()
            return self.__auth
        except Exception as e:
            pass

        self.__is_fallback = False
        self.__set_namespace()
        self.__auth = HttpNtlmAuth(self.__username, self.__password)
        try:
            await self._login()
            return self.__auth
        except Exception as e:
            pass

        self.__auth = BasicAuth(self.__username, self.__password)
        try:
            await self._login()
            return self.__auth
        except Exception as e:
            pass

        self.__auth = DigestAuth(self.__username, self.__password)
        try:
            await self._login()
            return self.__auth
        except Exception as e:
            pass

        raise Exception("Failed to guess auth method")
    
    def __set_namespace(self):
        
        self.__namespace = "http://videoos.net/2/XProtectCSServerCommand"
        if not self.__is_fallback:
            self.__endpoint = "/ManagementServer/ServerCommandService.svc"        # basic sur 76
            self.__login = f"{self.__namespace}/IServerCommandService/Login"
            self.__logout = f"{self.__namespace}/IServerCommandService/Logout"
            self.__config = f"{self.__namespace}/IServerCommandService/GetConfiguration"
            self.__recorder = f"{self.__namespace}/IServerCommandService/GetConfigurationRecorders"
        else:
            self.__endpoint = "/ServerAPI/ServerCommandService.asmx"              # ntlm sur 76
            self.__login = f"{self.__namespace}/Login"
            self.__logout = f"{self.__namespace}/Logout"
            self.__config = f"{self.__namespace}/GetConfiguration"
            self.__recorder = f"{self.__namespace}/GetRecorder"

    async def _refresh_token(self):
        if self.__token_expiration < datetime.datetime.now():
            await self._login()
            return True
        else:
            return False
    
    def _parse_stream_packet(self, data: bytes):
        format_str = '>HIHHHQQI'
        expected_size = struct.calcsize(format_str)
        bytes = struct.unpack(format_str, data[:expected_size])
        time = bytes[4]
        
        datetime_time = datetime.datetime.fromtimestamp(time/1000, datetime.timezone.utc)
        return datetime_time, data[expected_size:]
    
    def _parse_video_block(self, data: bytes):
        format_str = '>HHIIQQHHHH'
        expected_size = struct.calcsize(format_str)
        bytes = struct.unpack(format_str, data[:expected_size])
        header_extension = bytes[1]
        time = bytes[3]
        datetime_time = datetime.datetime.fromtimestamp(time/1000, datetime.timezone.utc)
        return datetime_time, data[expected_size+header_extension:]
    
    async def start_live(self, camera_guid: str, only_key_frames: bool = True) -> AsyncGenerator[np.ndarray, None]:
        if not self.__token:
            raise Exception("Not logged in")

        system_info = await self.get_system_info()
        if not system_info:
            raise Exception("Failed to get system info")
        if camera_guid not in system_info:
            raise Exception(f"Camera {camera_guid} not found")
        
        recorder = system_info[camera_guid]
        host = Resolver().resolve(recorder["hostName"])
        port = int(recorder["webServerUri"].split("/")[2].split(":")[1])
        protocol = recorder["webServerUri"].split(":")[0]

        self.requestid = 1
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, 
                port,
                ssl=self.__ssl_context if protocol == "https" else None
            ),
            timeout=TIMEOUT
        )

        connect = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<methodcall>"
            f"<requestid>{self.requestid}</requestid>"
            f"<methodname>connect</methodname>"
            f"<username />"
            f"<password />"
            f"<alwaysstdjpeg>no</alwaysstdjpeg>"
            f"<transcode><allframes>yes</allframes></transcode>"
            f"<connectparam>id={camera_guid}&amp;connectiontoken={self.__token}</connectparam>"
            f"<clientcapabilities>"
            f"<privacymask>no</privacymask>"
            f"<multipartdata>{'no' if only_key_frames else 'yes'}</multipartdata>"
            f"</clientcapabilities>"
            f"</methodcall>\r\n\r\n"
        ).encode("utf-8")
        self.requestid += 1
        self.writer.write(connect)
        await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)

        while True:
            line = await asyncio.wait_for(self.reader.readuntil(b"\r\n"), timeout=TIMEOUT)
            if not line or line == b"\r\n":
                break

        live = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<methodcall>"
            f"<requestid>{self.requestid}</requestid>"
            f"<methodname>live</methodname>"
            f"<sendinitialimage>yes</sendinitialimage>"
            f"</methodcall>\r\n\r\n"
        ).encode("utf-8")
        self.requestid += 1
        self.writer.write(live)
        await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)

        decoder = VideoDecoder()
        boundary = None

        while True:
            # new chunk of data
            data = b""
            while True:
                line = await asyncio.wait_for(self.reader.readuntil(b"\r\n"), timeout=TIMEOUT)
                data += line
                if not line or line == b"\r\n":
                    break
            
            logger.debug("--------------------------------")
            logger.debug("raw headers:")
            logger.debug(data)

            if (
                data.startswith(b"ImageResponse") or
                (boundary is not None and data.startswith(boundary)) or
                data.startswith(b"Content-length")
            ):

                headers = {}
                header_lines = data.decode("utf-8").split("\r\n")
                for line in header_lines:
                    if ":" in line:
                        key, value = line.split(":", 1)
                        headers[key.strip().lower()] = value.strip()
                
                content_length = int(headers.get("content-length", 0))
                content_type = headers.get("content-type", "")
                current_time = int(headers.get("current", 0)) / 1000
                datetime_time = datetime.datetime.fromtimestamp(current_time, datetime.timezone.utc)

                #print(current_time)
                logger.debug("decoded headers:")
                logger.debug(headers)

                if "multipart" in content_type:
                    for line in content_type.split(";"):
                        if "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip().lower()
                            value = value.strip()
                            if key == "boundary":
                                boundary = b"--" + value.strip().encode("utf-8") + b"\r\n"
                                break

                if content_length > 0 and content_type == "application/x-genericbytedata-octet-stream":
                    bytes = await asyncio.wait_for(self.reader.readexactly(content_length), timeout=TIMEOUT)

                    logger.debug("raw bytes:")
                    logger.debug(bytes[:10])

                    # raw jpeg
                    if bytes.startswith(b"\xff\xd8\xff\xe0"):
                        data = cv2.imdecode(np.frombuffer(bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                        yield data, datetime_time

                    # video block
                    elif bytes[0] == 0x00 and (
                        bytes[1] == 0x01 or
                        bytes[1] == 0x02 or
                        bytes[1] == 0x03 or
                        bytes[1] == 0x04 or
                        bytes[1] == 0x05 or
                        bytes[1] == 0x06 or
                        bytes[1] == 0x07 or
                        bytes[1] == 0x08 or
                        bytes[1] == 0x09 or
                        bytes[1] == 0x0a or
                        bytes[1] == 0x0b or
                        bytes[1] == 0x0c or
                        bytes[1] == 0x0d or
                        bytes[1] == 0x0e or
                        bytes[1] == 0x0f ):
                        
                        time_frame, data = self._parse_video_block(bytes)
                        for img in decoder.decode(data):
                            yield img, datetime_time

                    # stream packet
                    elif bytes.startswith(b"\x00\x10"):
                        time_frame, data = self._parse_stream_packet(bytes)
                        for img in decoder.decode(data):
                            yield img, datetime_time
                    else:
                        logger.error("Unknown content type", content_type, bytes[:10])
                        raise Exception("Unknown content type", content_type, bytes[:10])

                    # raw byte end with \r\n\r\n
                    await asyncio.wait_for(self.reader.readline(), timeout=TIMEOUT)
                    await asyncio.wait_for(self.reader.readline(), timeout=TIMEOUT)
                    
            elif data.startswith(b"<?xml"):
                pass
            elif data == b"\r\n":
                pass
            else:
                logger.debug(data)
                break # ??? end of stream ?

            if await self._refresh_token():
                connectupdate = (
                    f'<?xml version="1.0" encoding="UTF-8"?>'
                    f"<methodcall>"
                    f"<requestid>{self.requestid}</requestid>"
                    f"<methodname>connectupdate</methodname>"
                    f"<connectparam>id={camera_guid}&amp;connectiontoken={self.__token}</connectparam>"
                    f"</methodcall>\r\n\r\n"
                ).encode("utf-8")
                self.requestid += 1
                self.writer.write(connectupdate)
                await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)

    async def start_replay(self, camera_guid: str, from_time: datetime.datetime, to_time: datetime.datetime, gap: int = 0, only_key_frames: bool = True) -> AsyncGenerator[Tuple[np.ndarray, str], None]:
        if not self.__token:
            raise Exception("Not logged in")
        if from_time > to_time:
            raise ValueError("from_time must be before to_time")
        if from_time > datetime.datetime.now().astimezone(datetime.timezone.utc):
            raise ValueError("from_time must be in the past")
        if to_time > datetime.datetime.now().astimezone(datetime.timezone.utc):
            raise ValueError("to_time must be in the past")
        
        system_info = await self.get_system_info()
        if not system_info:
            raise Exception("Failed to get system info")
        if camera_guid not in system_info:
            raise Exception(f"Camera {camera_guid} not found")
        
        recorder = system_info[camera_guid]
        host = Resolver().resolve(recorder["hostName"])
        port = int(recorder["webServerUri"].split("/")[2].split(":")[1])
        protocol = recorder["webServerUri"].split(":")[0]

        self.requestid = 1
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(
                host, 
                port,
                ssl=self.__ssl_context if protocol == "https" else None
            ),
            timeout=TIMEOUT
        )

        connect = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<methodcall>"
            f"<requestid>{self.requestid}</requestid>"
            f"<methodname>connect</methodname>"
            f"<username />"
            f"<password />"
            f"<alwaysstdjpeg>no</alwaysstdjpeg>"
            f"<transcode><allframes>yes</allframes></transcode>"
            f"<connectparam>id={camera_guid}&amp;connectiontoken={self.__token}</connectparam>"
            f"<clientcapabilities>"
            f"<privacymask>no</privacymask>"
            f"<multipartdata>{'no' if only_key_frames else 'yes'}</multipartdata>"
            f"</clientcapabilities>"
            f"</methodcall>\r\n\r\n"
        ).encode("utf-8")
        self.requestid += 1
        self.writer.write(connect)
        await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)

        while True:
            line = await asyncio.wait_for(self.reader.readuntil(b"\r\n"), timeout=TIMEOUT)
            if not line or line == b"\r\n":
                break

        goto = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<methodcall>"
            f"<requestid>{self.requestid}</requestid>"
            f"<methodname>goto</methodname>"
            f"<time>{int(from_time.timestamp() * 1000)}</time>"
            f"<keyframesonly>{'yes' if only_key_frames else 'no'}</keyframesonly>"
            f"</methodcall>\r\n\r\n"
        ).encode("utf-8")
        self.requestid += 1
        self.writer.write(goto)
        await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)

        decoder = VideoDecoder()
        boundary = None

        while True:
            # new chunk of data
            data = b""
            while True:
                line = await asyncio.wait_for(self.reader.readuntil(b"\r\n"), timeout=TIMEOUT)
                data += line
                if not line or line == b"\r\n":
                    break
            
            logger.debug("--------------------------------")
            logger.debug("raw headers:")
            logger.debug(data)

            if (
                data.startswith(b"ImageResponse") or
                (boundary is not None and data.startswith(boundary)) or
                data.startswith(b"Content-length")
            ):

                headers = {}
                header_lines = data.decode("utf-8").split("\r\n")
                for line in header_lines:
                    if ":" in line:
                        key, value = line.split(":", 1)
                        headers[key.strip().lower()] = value.strip()
                
                content_length = int(headers.get("content-length", 0))
                content_type = headers.get("content-type", "")
                current_time = int(headers.get("current", 0)) / 1000
                datetime_time = datetime.datetime.fromtimestamp(current_time, datetime.timezone.utc)

                #print(current_time)
                logger.debug("decoded headers:")
                logger.debug(headers)

                if "multipart" in content_type:
                    for line in content_type.split(";"):
                        if "=" in line:
                            key, value = line.split("=", 1)
                            key = key.strip().lower()
                            value = value.strip()
                            if key == "boundary":
                                boundary = b"--" + value.strip().encode("utf-8") + b"\r\n"
                                break

                if datetime_time > to_time:
                    break

                if content_length > 0 and content_type == "application/x-genericbytedata-octet-stream":
                    bytes = await asyncio.wait_for(self.reader.readexactly(content_length), timeout=TIMEOUT)

                    logger.debug("raw bytes:")
                    logger.debug(bytes[:10])

                    # raw jpeg
                    if bytes.startswith(b"\xff\xd8\xff\xe0"):
                        data = cv2.imdecode(np.frombuffer(bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
                        yield data, datetime_time

                    # video block
                    elif bytes[0] == 0x00 and (
                        bytes[1] == 0x01 or
                        bytes[1] == 0x02 or
                        bytes[1] == 0x03 or
                        bytes[1] == 0x04 or
                        bytes[1] == 0x05 or
                        bytes[1] == 0x06 or
                        bytes[1] == 0x07 or
                        bytes[1] == 0x08 or
                        bytes[1] == 0x09 or
                        bytes[1] == 0x0a or
                        bytes[1] == 0x0b or
                        bytes[1] == 0x0c or
                        bytes[1] == 0x0d or
                        bytes[1] == 0x0e or
                        bytes[1] == 0x0f ):
                        
                        time_frame, data = self._parse_video_block(bytes)
                        for img in decoder.decode(data):
                            yield img, datetime_time

                    # stream packet
                    elif bytes.startswith(b"\x00\x10"):
                        time_frame, data = self._parse_stream_packet(bytes)
                        for img in decoder.decode(data):
                            yield img, datetime_time
                    else:
                        logger.error("Unknown content type", content_type, bytes[:10])
                        raise Exception("Unknown content type", content_type, bytes[:10])

                    # raw byte end with \r\n\r\n
                    await asyncio.wait_for(self.reader.readline(), timeout=TIMEOUT)
                    await asyncio.wait_for(self.reader.readline(), timeout=TIMEOUT)

                    next = (
                        f'<?xml version="1.0" encoding="UTF-8"?>'
                        f"<methodcall>"
                        f"<requestid>{self.requestid}</requestid>"
                        f"<methodname>next</methodname>"
                        f"</methodcall>\r\n\r\n"
                    ).encode("utf-8")
                    self.requestid += 1
                    self.writer.write(next)

                await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)
            elif data.startswith(b"<?xml"):
                pass
            elif data == b"\r\n":
                pass
            else:
                logger.debug(data)
                break # ??? end of stream ?

            if await self._refresh_token():
                connectupdate = (
                    f'<?xml version="1.0" encoding="UTF-8"?>'
                    f"<methodcall>"
                    f"<requestid>{self.requestid}</requestid>"
                    f"<methodname>connectupdate</methodname>"
                    f"<connectparam>id={camera_guid}&amp;connectiontoken={self.__token}</connectparam>"
                    f"</methodcall>\r\n\r\n"
                ).encode("utf-8")
                self.requestid += 1
                self.writer.write(connectupdate)
                await asyncio.wait_for(self.writer.drain(), timeout=TIMEOUT)


async def process_replay_stream(client, camera_guid, from_time, to_time, gap, name):
    async for frame, time in client.start_replay(camera_guid, from_time, to_time, gap):
        print(name, frame.shape, time)

async def test_dual_stream(host: str, port: int):   
    client1 = CameraClient(host, port)
    client2 = CameraClient(host, port)
    
    cameras = await client1.get_system_info()
    camera_guid = next(iter(cameras))
    
    now = datetime.datetime.now()
    
    from_time1 = now.replace(hour=7, minute=0, second=0)
    to_time1 = now.replace(hour=7, minute=5, second=0)
    
    from_time2 = now.replace(hour=8, minute=0, second=0)
    to_time2 = now.replace(hour=8, minute=5, second=0)

    gap = 5
    
    # Lancer les deux flux en parallèle
    await asyncio.gather(
        process_replay_stream(client1, camera_guid, from_time1, to_time1, gap, "stream1"),
        process_replay_stream(client2, camera_guid, from_time2, to_time2, gap, "stream2")
    )

async def test_genetec():
    parser = argparse.ArgumentParser(description="Client de contrôle des caméras")
    parser.add_argument("--host", default="192.168.20.72", help="Adresse IP du serveur")
    parser.add_argument("--port", type=int, default=7778, help="Port du serveur")

    args = parser.parse_args()

    client = GenetecCameraClient(args.host, args.port)
    try:
        cameras = await client.get_system_info()
        print("Informations système:", cameras)

        if cameras:
            first_guid = next(iter(cameras))
            print("GUID de la première caméra:", first_guid)

            streams = client.start_live(first_guid, False)
            img = await anext(streams)
            cv2.imwrite("test.jpg", img)

    except Exception as e:
        print(f"Erreur lors de l'exécution: {e}")

async def test_milestone():
    async with MilestoneCameraClient("192.168.20.50", 80, "AcicMilestoneGrab", "UhH66PjFSTWrrbiH2RiM--" , auth_method="ntml") as client:
    #async with MilestoneCameraClient("192.168.20.232", 443, 'Admininstrator', "7ednHuLXNThoQg5p--", is_fallback=True, auth_method="ntlm") as client:
    #async with MilestoneCameraClient("192.168.20.76", 443, 'bb', "Acicacic1-", is_fallback=False, auth_method="basic") as client:
    #async with MilestoneCameraClient("192.168.20.76", 443, 'bbarrie', "RedHeel44+", is_fallback=True, auth_method="ntlm") as client:
    #async with MilestoneCameraClient("192.168.20.232", 443, 'sbakkouche', 'YxCt4gLEHB758F', True, "ntlm") as client:
    #async with MilestoneCameraClient("192.168.20.76", 443, 'bbarrie', "RedHeel44+", is_fallback=True) as client:
        cameras = await client.get_system_info()
        print(f"cameras : {cameras}")
        first_guid = next(iter(cameras))
        print(f"first_guid : {first_guid}")

        #streams = client.start_live(first_guid, False)
        now = datetime.datetime.now()
        
        from_time = now.replace(hour=12, minute=10, second=0).astimezone(datetime.timezone.utc)
        to_time = now.replace(hour=12, minute=11, second=0).astimezone(datetime.timezone.utc)

        container, codec = "webm", "vp8"
        output = av.open("output.webm", mode='w', format=container)
        stream = output.add_stream(codec, rate=1)
        stream.pix_fmt = 'yuv420p'
        
        try:
            prev_time = None
            with tqdm() as pbar:
                streams = client.start_replay(first_guid, from_time, to_time, 0, False)
                async for img, time_frame in streams:
                    if prev_time:
                        elapsed = (time_frame - prev_time).total_seconds()
                        pbar.set_description(f"Time between frames: {elapsed:.2f} seconds")
                    prev_time = time_frame
                    pbar.update(1)

                    frame = av.VideoFrame.from_ndarray(img, format='bgr24')
                    packet = stream.encode(frame)
                    output.mux(packet)
                
        except Exception as e:
            print(e)
        finally:
            output.close()

if __name__ == "__main__":
    asyncio.run(test_milestone())
