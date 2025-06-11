from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Dict, Optional, Union, Literal, Annotated
from typing_extensions import TypedDict

import requests
import datetime

from fastapi import Depends, APIRouter
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from pydantic import BaseModel, Field, Extra, validator

from starlette.requests import Request
from starlette.responses import StreamingResponse

class Settings:
    environment: str = 'development'
    host: str = "127.0.0.1"
    port: int = 8444
    username: str = "administrator"
    password: str = "ACIC"
    version: List[int] = [0, 17]

    def has_feature(self, since):
        for i in range(len(self.version)):
            if self.version[i] > since[i]:
                return 1
            if self.version[i] < since[i]:
                return -1
        return 0

class ACIC:
    session = requests.Session()
    settings = Settings()
    security = HTTPBasic()

    session.verify = False
    session.auth = (settings.username, settings.password)

def AcicAuthenticate(credentials: HTTPBasicCredentials = Depends(ACIC.security)):
    ACIC.session.auth = (credentials.username, credentials.password)

########################################################

class AcicBackend(ABC):
    router = APIRouter()
    model = BaseModel
    enable_get_all = True
    enable_get_all_list = False
    enable_get_one = True
    enable_post = True
    enable_put = True
    enable_delete = True
    version = [0, 0]

    @classmethod
    def get_router(cls):
        return cls.router

    @classmethod
    def create_routes(cls):
        pass

    @staticmethod
    def get(uri):
        return ACIC.session.get(f"https://{ACIC.settings.host}:{ACIC.settings.port}/api{uri}").json()

    @staticmethod
    def get_raw(uri):
        return ACIC.session.get(f"https://{ACIC.settings.host}:{ACIC.settings.port}/api{uri}")

    @staticmethod
    def post(uri, data):
        return ACIC.session.post(f"https://{ACIC.settings.host}:{ACIC.settings.port}/api/{uri}", json=data).text

    @staticmethod
    def put(uri, data):
        return ACIC.session.put(f"https://{ACIC.settings.host}:{ACIC.settings.port}/api/{uri}", json=data).text

    @staticmethod
    def delete(uri):
        return ACIC.session.delete(f"https://{ACIC.settings.host}:{ACIC.settings.port}/api/{uri}").text

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        if ACIC.settings.has_feature(cls.version) >= 0:
            cls.create_routes()

class AcicBackendGeneralGuid(AcicBackend):
    @classmethod
    def create_routes(cls):

        if cls.enable_get_all:
            @cls.router.get("", response_model=Dict[str, cls.model])
            def get_all(request: Request):
                uri = f"{cls.router.prefix}"
                return cls.get(uri)

        if cls.enable_get_all_list:
            @cls.router.get("", response_model=List[cls.model])
            def get_all_list(request: Request):
                uri = f"{cls.router.prefix}"
                return cls.get(uri)

        if cls.enable_get_one:
            @cls.router.get("/{guid}", response_model=cls.model)
            def get_one(request: Request, guid: str):
                uri = f"{cls.router.prefix}/{guid}"
                return cls.get(uri)

        if cls.enable_post:
            @cls.router.post("")
            async def insert(request: Request, data: cls.model):
                uri = f"{cls.router.prefix}"
                data = await request.json()
                return cls.post(uri, data)

        if cls.enable_put:
            @cls.router.put("/{guid}")
            async def update(request: Request, guid: str, data: cls.model):
                uri = f"{cls.router.prefix}/{guid}"
                data = await request.json()
                return cls.put(uri, data)

        if cls.enable_delete:
            @cls.router.delete("/{guid}")
            def delete(request: Request, guid: str):
                uri = f"{cls.router.prefix}/{guid}"
                return cls.delete(uri)

class AcicBackendGeneral(AcicBackend):
    @classmethod
    def create_routes(cls):
        if cls.enable_get_all:
            @cls.router.get("", response_model=List[cls.model])
            def get_all(request: Request):
                uri = f"{cls.router.prefix}"
                return cls.get(uri)

        if cls.enable_get_one:
            @cls.router.get("", response_model=cls.model)
            def get(request: Request):
                uri = f"{cls.router.prefix}"
                return cls.get(uri)

        if cls.enable_post:
            @cls.router.post("")
            async def insert(request: Request, data: cls.model):
                uri = f"{cls.router.prefix}"
                data = await request.json()
                return cls.post(uri, data)

        if cls.enable_put:
            @cls.router.put("")
            async def update(request: Request, data: cls.model):
                uri = f"{cls.router.prefix}"
                data = await request.json()
                return cls.put(uri, data)

        if cls.enable_delete:
            @cls.router.delete("")
            def delete(request: Request):
                uri = f"{cls.router.prefix}"
                return cls.delete(uri)

class AcicBackendStreamAppGuid(AcicBackend):
    @classmethod
    def create_routes(cls):

        if cls.enable_get_all:
            @cls.router.get("/{streamid}/{appname}")
            def get_all(request: Request, streamid: int, appname: str):
                uri = f"{cls.router.prefix}/{streamid}/{appname}"
                return cls.get(uri)

        if cls.enable_get_one:
            @cls.router.get("/{streamid}/{appname}/{guid}", response_model=cls.model)
            def get_one(request: Request, streamid: int, appname: str, guid: str):
                uri = f"{cls.router.prefix}/{streamid}/{appname}/{guid}"
                return cls.get(uri)

        if cls.enable_post:
            @cls.router.post("/{streamid}/{appname}")
            async def insert(request: Request, streamid: int, appname: str, data: cls.model):
                uri = f"{cls.router.prefix}/{streamid}/{appname}"
                data = await request.json()
                return cls.post(uri, data)

        if cls.enable_put:
            @cls.router.put("/{streamid}/{appname}/{guid}")
            async def update(request: Request, streamid: int, appname: str, guid: str, data: cls.model):
                uri = f"{cls.router.prefix}/{streamid}/{appname}/{guid}"
                data = await request.json()
                return cls.put(uri, data)

        if cls.enable_delete:
            @cls.router.delete("/{streamid}/{appname}/{guid}")
            def delete(request: Request, streamid: int, appname: str, guid: str):
                uri = f"{cls.router.prefix}/{streamid}/{appname}/{guid}"
                return cls.delete(uri)

class AcicBackendStreamGuid(AcicBackend):
    @classmethod
    def create_routes(cls):
        if cls.enable_get_all:
            @cls.router.get("/{streamid}")
            def get_all(request: Request, streamid: int):
                uri = f"{cls.router.prefix}/{streamid}"
                return cls.get(uri)

        if cls.enable_get_one:
            @cls.router.get("/{streamid}/{guid}", response_model=cls.model)
            def get_one(request: Request, streamid: int, guid: str):
                uri = f"{cls.router.prefix}/{streamid}/{guid}"
                return cls.get(uri)

        if cls.enable_post:
            @cls.router.post("/{streamid}")
            async def insert(request: Request, streamid: int, data: cls.model):
                uri = f"{cls.router.prefix}/{streamid}"
                data = await request.json()
                return cls.post(uri, data)

        if cls.enable_put:
            @cls.router.put("/{streamid}/{guid}")
            async def update(request: Request, streamid: int, guid: str, data: cls.model):
                uri = f"{cls.router.prefix}/{streamid}/{guid}"
                data = await request.json()
                return cls.put(uri, data)

        if cls.enable_delete:
            @cls.router.delete("/{streamid}/{guid}")
            def delete(request: Request, streamid: int, guid: str):
                uri = f"{cls.router.prefix}/{streamid}/{guid}"
                return cls.delete(uri)

########################################################
#       Outputs generator
########################################################

class AdamServer(AcicBackendGeneralGuid):
    class Model(BaseModel):
        label: str = "label"
        address: str = "192.168.20.118"
        port: int = 80
        login: str = "root"
        password: str = "00000000"

    router = APIRouter(
        prefix="/adamServer",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 14]

class ExacqServer(AdamServer):
    router = APIRouter(
        prefix="/exacqServer",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )

class FtpServer(AcicBackendGeneralGuid):
    class Model(BaseModel):
        label: str = "label"
        address: str = "192.168.20.118"
        port: int = 80
        login: str = "root"
        password: str = "00000000"
        protocol: str = "ftp"
        validateCertificate: bool = False

    router = APIRouter(
        prefix="/ftpServer",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 14]

class HttpServer(AcicBackendGeneralGuid):
    class Model(BaseModel):
        label: str = "label"
        address: str = "192.168.20.118"
        port: int = 80
        login: str = "root"
        password: str = "00000000"
        protocol: str = "http"
        validateCertificate: bool = False
        authenticationMethod: str = "digest"

    router = APIRouter(
        prefix="/httpServer",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 14]

class ModbusServer(AcicBackendGeneralGuid):
    class Model(BaseModel):
        label: str = "label"
        address: str = "192.168.20.118"
        port: int = 80

    router = APIRouter(
        prefix="/modbusServer",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 14]

class SmtpServer(AcicBackendGeneralGuid):
    class Model(BaseModel):
        label: str = "label"
        address: str = "192.168.20.118"
        port: int = 80
        login: str = "root@acic.biz"
        password: str = "00000000"
        method: Enum("methodType", {"none": "none", "SSL": "SSL", "TLS": "TLS"}) = "none"
        validateCertificate: bool = False
        from_: str = Field("root@acic.biz", alias="from")

    router = APIRouter(
        prefix="/smtpServer",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 14]

class TcpServer(AcicBackendGeneralGuid):
    class Model(BaseModel):
        label: str = "label"
        address: str = "192.168.20.118"
        port: int = 80

    router = APIRouter(
        prefix="/tcpServer",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 14]

########################################################
#        
########################################################

class AIService(AcicBackendGeneral):
    class Model(BaseModel):
        type: Enum("CudaType", {"CUDA": "CUDA", "Model Server": "Model Server"}) = "CUDA"
        address: str = "192.168.20.45"
        port: int = 53211
        configuration: Optional[str] = "http://192.168.20.45:53211/config"

    router = APIRouter(
        prefix="/aiService",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]
    enable_get_all = False
    enable_post = False


class LPRService(AcicBackendGeneral):
    class Model(BaseModel):
        address: str = "192.168.20.45"
        port: int = 53211
        configuration: Optional[str] = "http://192.168.20.45:53211/config"

    router = APIRouter(
        prefix="/lprService",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 15]
    enable_get_all = False
    enable_post = False

class AnalyticsRules(AcicBackendStreamAppGuid):
    class Model(BaseModel):
        active: bool

    router = APIRouter(
        prefix="/analyticsRules",
        tags=["streams"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 15]
    enable_post = False
    enable_delete = False

class Applications(AcicBackendGeneral):
    class Model1(BaseModel):
        id: str
        name: str

    class Model2(Model1):
        events: List[str]

    router = APIRouter(
        prefix="/applications",
        tags=["default"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model2 if ACIC.settings.has_feature([0, 14]) >= 0 else Model1
    version = [0, 11]
    enable_get_one = False
    enable_put = False
    enable_post = False
    enable_delete = False

class Authenticate(AcicBackendGeneral):
    class Model(BaseModel):
        user: str = "JaneDoe"
        password: str = "Password123456!"
        ip: str = "192.168.20.100"

    router = APIRouter(
        prefix="/authenticate",
        tags=["users"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 5]

    enable_get_one = False
    enable_get_all = False
    enable_put = False
    enable_delete = False

class Backup(AcicBackendGeneral):
    class Model(BaseModel):
        all: bool = Field(alias='global')
        streams: List[str] = []

    router = APIRouter(
        prefix="/backup",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_put = False
    enable_post = False
    enable_delete = False

    @classmethod
    def create_routes(cls):
        @cls.router.post("")
        async def download(request: Request, data: cls.model):
            uri = f"{cls.router.prefix}"
            data = await request.json()
            content = cls.post(uri, data)
            return StreamingResponse(content, media_type='application/octet-stream', headers={"Content-Disposition": "attachment; filename=backup.mvb"})

class Restore(AcicBackendGeneral):
    class Model(BaseModel):
        restorePoint: str
        all: bool = Field(alias='global')
        streams: List[Dict] = []

    class RestorePoint(BaseModel):
        firmware: str = "1.26.2020.09.04.17.06.03"
        host: str = "acic"
        ip: str = "192.168.20.115 000C29FFCDFFE419"
        data: str = "20201015T121609.669442Z"
        streams: str = "5"
        int: str = "1"
        stream1_name: str = "stream_1"
        stream2_name: Optional[str]

        class Config:
            extra = Extra.allow

    class BackupFile(BaseModel):
        fileName: str
        data: str

    router = APIRouter(
        prefix="/restore",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    @classmethod
    def create_routes(cls):
        # TODO: The apply method return a "<html><head><script type="text/javascript">function redirect() { window.top.location.href='Reboot.cgi'}</script></head><body onload="redirect()"> </body></html>" instead of JSON
        @cls.router.post("")
        async def apply(request: Request, data: cls.model):
            uri = f"{cls.router.prefix}"
            data = await request.json()
            return cls.post(uri, data)

        @cls.router.post("Point")
        async def upload(request: Request, data: cls.BackupFile):
            uri = f"{cls.router.prefix}Point"
            data = await request.json()
            return cls.post(uri, data)

        @cls.router.get("Point/{guid}", response_model=cls.RestorePoint)
        def get(request: Request, guid: str):
            uri = f"{cls.router.prefix}Point/{guid}"
            return cls.get(uri)

class Calendar(AcicBackendGeneralGuid):

    class Model1(BaseModel):
        label: str = "Calendar 1"
        type: Literal["WeeklyCalendar"] = "WeeklyCalendar"
        data: TypedDict("CalendarData", {
            "days": List[TypedDict("DayCalendar", {
                "dayOfTheWeek": int,
                "periods": List[Annotated[str, Field(pattern=r"^T([01]\d|2[0-3]):[0-5]\d/T(24:00|2[0-3]:[0-5]\d|[01]\d:[0-5]\d)$")]]
            })],
        })

    class Model2(BaseModel):
        label: str = "Calendar 1"
        type: Literal["WeeklyByDatesCalendar"] = "WeeklyByDatesCalendar"
        data: TypedDict("CalendarData", {
            "days": List[TypedDict("DayCalendar", {
                "dayOfTheWeek": int,
                "periods": List[Annotated[str, Field(pattern=r"^T([01]\d|2[0-3]):[0-5]\d/T(24:00|2[0-3]:[0-5]\d|[01]\d:[0-5]\d)$")]]
            })],
            "startDate": Optional[str],
            "stopDate": Optional[str]
        })

    router = APIRouter(
        prefix="/calendars",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )

    model = Union[Model1, Model2]
    version = [0, 13]

class CameraFeatures(AcicBackendGeneral):
    class Model(BaseModel):
        ip: str = "192.168.20.192"
        port: int = 80
        login: str = "root"
        password: str = "root"

    router = APIRouter(
        prefix="/cameraFeatures",
        tags=["default"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 7]

    @classmethod
    def create_routes(cls):
        @cls.router.post("")
        async def scan(request: Request, data: cls.model):
            uri = f"{cls.router.prefix}"
            data = await request.json()
            return cls.post(uri, data)

class CameraAnomaly(AcicBackendGeneral):
    class ModelStream(BaseModel):
        blurDetection: Dict
        cameraDisplacement: Dict
        videoLoss: Dict
        videoQualityFalloff: Dict
        wrongExposure: Dict

    class ModelStatus(BaseModel):
        cameraDisplacement: float = 0
        videoQualityFalloff: float = 0

    router = APIRouter(
        prefix="/cameraAnomaly",
        tags=["streams"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = ModelStream
    version = [0, 12]

    @classmethod
    def create_routes(cls):
        @cls.router.get("/{streamid}", response_model=cls.ModelStream)
        async def get_all(request: Request, streamid: int):
            uri = f"{cls.router.prefix}/{streamid}"
            return cls.get(uri)

        @cls.router.get("/{streamid}/{feature}", response_model=Dict)
        async def get_one(request: Request, streamid: int, feature: str):
            uri = f"{cls.router.prefix}/{streamid}/{feature}"
            return cls.get(uri)

        @cls.router.put("/{streamid}")
        async def get_all(request: Request, streamid: int, data: cls.ModelStream):
            uri = f"{cls.router.prefix}/{streamid}"
            data = await request.json()
            return cls.put(uri, data)

        @cls.router.put("/{streamid}/{feature}")
        async def get_one(request: Request, streamid: int, feature: str, data: Dict):
            uri = f"{cls.router.prefix}/{streamid}/{feature}"
            data = await request.json()
            return cls.put(uri, data)

        @cls.router.get("Description")
        async def get_description(request: Request):
            uri = f"{cls.router.prefix}/Description"
            return cls.get(uri)

class DefaultGateway(AcicBackendGeneral):
    class Model(BaseModel):
        ip: str = "192.168.20.254"

    router = APIRouter(
        prefix="/defaultGateway",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 6]

    enable_get_one = True
    enable_get_all = False
    enable_put = True
    enable_delete = False
    enable_post = False

class DeviceSources(AcicBackendGeneral):

    # deprecated.
    class Model(BaseModel):
        class Config:
            extra = Extra.allow

    router = APIRouter(
        prefix="/deviceSources",
        tags=["default"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 3]

    enable_get_one = False
    enable_get_all = False
    enable_put = False
    enable_delete = False
    enable_post = False

class DiagnosticDump(AcicBackendGeneral):
    router = APIRouter(
        prefix="/diagnosticDump",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_put = False
    enable_post = False
    enable_delete = False

    @classmethod
    def create_routes(cls):
        @cls.router.get("")
        async def download(request: Request):
            uri = f"{cls.router.prefix}"
            content = cls.get_raw(uri)
            return StreamingResponse(content, media_type='application/octet-stream', headers={"Content-Disposition": "attachment; filename=diagdump.tar.gz"})

class Doc(AcicBackendGeneralGuid):
    router = APIRouter(
        prefix="/doc",
        tags=["default"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_put = False
    enable_post = False
    enable_delete = False

    @classmethod
    def create_routes(cls):
        @cls.router.get("/{appname}")
        async def download(request: Request, appname: str):
            uri = f"{cls.router.prefix}/{appname}"
            content = cls.get_raw(uri)
            return StreamingResponse(content, media_type='application/octet-stream', headers={"Content-Disposition": "attachment; filename="+appname+".pdf"})

class Firmware(AcicBackendGeneral):
    class Model(BaseModel):
        filename: str = "MvFirmware.mvf"
        data: str
        dataLength: int
        key: str

    router = APIRouter(
        prefix="/firmware",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 8]

    enable_get_one = False
    enable_get_all = False
    enable_put = True
    enable_post = False
    enable_delete = False

class Halt(AcicBackendGeneral):
    class Model(BaseModel):
        pass

    router = APIRouter(
        prefix="/halt",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_put = True
    enable_post = False
    enable_delete = False

class License(AcicBackendGeneral):
    class Model(BaseModel):
        required: str
        current: str
        expiry: str

    class ModelUpload(BaseModel):
        address: str = "192.168.20.26"
        port: int = 3333
        license: str

    class ModelRequest(BaseModel):
        address: str = "192.168.20.26"
        port: int = 3333
        license: str

    class ModelServer(BaseModel):
        name: str
        version: str
        total: int
        available: int
        useLocally: int
        expiry: str

    router = APIRouter(
        prefix="/license",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    @classmethod
    def create_routes(cls):
        @cls.router.post("")
        async def upload_license(request: Request, data: cls.ModelUpload):
            uri = f"{cls.router.prefix}"
            data = await request.json()
            return cls.post(uri, data)

        @cls.router.post("Request")
        async def ask_license(request: Request, data: cls.ModelRequest):
            uri = f"{cls.router.prefix}Request"
            data = await request.json()
            content = cls.post(uri, data)
            return StreamingResponse(content, media_type='application/octet-stream',
                                     headers={"Content-Disposition": "attachment; filename=request.bin"})

        @cls.router.get("Status/{streamid}", response_model=List[cls.Model])
        def get_stream_license(request: Request, streamid: int):
            uri = f"{cls.router.prefix}Status/{streamid}"
            return cls.get(uri)

        @cls.router.get("ServerStatus", response_model=List[cls.ModelServer])
        def get_server_license(request: Request):
            uri = f"{cls.router.prefix}ServerStatus"
            return cls.get(uri)

class LocalDns(AcicBackendGeneralGuid):
    class Model(BaseModel):
        address: str = "192.168.20.254"
        name: str = "acic"

    router = APIRouter(
        prefix="/localDns",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_get_all_list = True
    enable_put = True
    enable_delete = True
    enable_post = True

class MetaDataRecorder(AcicBackendGeneral):
    class Model(BaseModel):
        recording: int = Field(1, ge=0, le=1)
        retentionDays: Optional[int] = 31
        minFreeSpace: Optional[int] = 500
        chunkDuration: Optional[int] = 60
        clearRecording: Optional[int] = Field(None, ge=0, le=1)

    router = APIRouter(
        prefix="/metaDataRecorder",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 12]

    enable_post = False
    enable_get_all = False
    enable_delete = False

class Network(AcicBackendGeneralGuid):
    class Model(BaseModel):
        name: str = "eth0"
        ip: str = "192.168.20.1"
        netmask: str = "255.255.255.0"
        dhcp: bool = False

    router = APIRouter(
        prefix="/network",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 6]

    enable_get_one = False
    enable_get_all = False
    enable_get_all_list = True
    enable_put = True
    enable_delete = True
    enable_post = False

class Ntp(AcicBackendGeneral):
    class Model(BaseModel):
        ip: str

    router = APIRouter(
        prefix="/ntp",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 6]

    @classmethod
    def create_routes(cls):
        @cls.router.get("", response_model=List[str])
        async def get_all(request: Request):
            uri = f"{cls.router.prefix}"
            return cls.get(uri)

        @cls.router.post("/{ip}")
        async def add(request: Request, ip: str):
            uri = f"{cls.router.prefix}/{ip}"
            return cls.post(uri, None)

        @cls.router.delete("/{ip}")
        async def delete(request: Request, ip: str):
            uri = f"{cls.router.prefix}/{ip}"
            return cls.delete(uri)

class Output(AcicBackendGeneral):
    class Model(BaseModel):
        class Config:
            extra = Extra.allow

    router = APIRouter(
        prefix="/outputs",
        tags=["outputs"],
        dependencies=[Depends(AcicAuthenticate)],
    )

    model = Model
    version = [0, 14]

    @classmethod
    def create_routes(cls):
        @cls.router.get("Descriptions")
        async def get_descriptions(request: Request):
            uri = f"{cls.router.prefix}Descriptions"
            return cls.get(uri)

        @cls.router.get("Features")
        async def get_features(request: Request):
            uri = f"{cls.router.prefix}Features"
            return cls.get(uri)

        @cls.router.get("/{module_id}")
        async def get_config(request: Request, module_id: str):
            uri = f"{cls.router.prefix}/{module_id}"
            return cls.get(uri)

        @cls.router.put("/{module_id}")
        async def update_config(request: Request, module_id: str):
            uri = f"{cls.router.prefix}/{module_id}"
            data = await request.json()
            return cls.put(uri, data)

        @cls.router.get("/{module_id}/rules")
        async def get_all(request: Request, module_id: str):
            uri = f"{cls.router.prefix}/{module_id}/rules"
            return cls.get(uri)

        @cls.router.post("/{module_id}/rules")
        async def add_rule(request: Request, module_id: str):
            uri = f"{cls.router.prefix}/{module_id}/rules"
            data = await request.json()
            return cls.post(uri, data)

        @cls.router.get("/{module_id}/{rule_id}")
        async def get_one(request: Request, module_id: str, rule_id: int):
            uri = f"{cls.router.prefix}/{module_id}/{rule_id}"
            return cls.get(uri)

        @cls.router.put("/{module_id}/{rule_id}")
        async def update_one(request: Request, module_id: str, rule_id: int):
            uri = f"{cls.router.prefix}/{module_id}/{rule_id}"
            data = await request.json()
            return cls.put(uri, data)

        @cls.router.delete("/{module_id}/{rule_id}")
        async def delete_one(request: Request, module_id: str, rule_id: int):
            uri = f"{cls.router.prefix}/{module_id}/{rule_id}"
            return cls.delete(uri)

        # stream related
        @cls.router.get("/{stream_id}/{appname}/{module_id}")
        async def stream_get_config(request: Request, stream_id: int, appname: str, module_id: str):
            uri = f"{cls.router.prefix}/{stream_id}/{appname}/{module_id}"
            return cls.get(uri)

        @cls.router.put("/{stream_id}/{appname}/{module_id}")
        async def stream_update_config(request: Request, stream_id: int, appname: str, module_id: str):
            uri = f"{cls.router.prefix}/{stream_id}/{appname}/{module_id}"
            data = await request.json()
            return cls.put(uri, data)

        @cls.router.get("/{stream_id}/{appname}/{module_id}/rules")
        async def stream_get_all(request: Request, stream_id: int, appname: str, module_id: str):
            uri = f"{cls.router.prefix}/{stream_id}/{appname}/{module_id}/rules"
            return cls.get(uri)

        @cls.router.post("/{stream_id}/{appname}/{module_id}/rules")
        async def stream_add_rule(request: Request, stream_id: int, appname: str, module_id: str):
            uri = f"{cls.router.prefix}/{stream_id}/{appname}/{module_id}/rules"
            data = await request.json()
            return cls.post(uri, data)

        @cls.router.get("/{stream_id}/{appname}/{module_id}/{rule_id}")
        async def stream_get_one(request: Request, stream_id: int, appname: str, module_id: str, rule_id: int):
            uri = f"{cls.router.prefix}/{stream_id}/{appname}/{module_id}/{rule_id}"
            return cls.get(uri)

        @cls.router.put("/{stream_id}/{appname}/{module_id}/{rule_id}")
        async def stream_update_one(request: Request, stream_id: int, appname: str, module_id: str, rule_id: int):
            uri = f"{cls.router.prefix}/{stream_id}/{appname}/{module_id}/{rule_id}"
            data = await request.json()
            return cls.put(uri, data)

        @cls.router.delete("/{stream_id}/{appname}/{module_id}/{rule_id}")
        async def stream_delete_one(request: Request, stream_id: int, appname: str, module_id: str, rule_id: int):
            uri = f"{cls.router.prefix}/{stream_id}/{appname}/{module_id}/{rule_id}"
            return cls.delete(uri)

class Ping(AcicBackendGeneralGuid):
    class Model(BaseModel):
        user: str
        ip: str

    router = APIRouter(
        prefix="/ping",
        tags=["users"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 6]

    enable_get_one = False
    enable_get_all = False
    enable_put = True
    enable_post = False
    enable_delete = False

class ProductVersion(AcicBackendGeneral):
    class Model(BaseModel):
        version: str
        buildTag: str
        products: List[TypedDict("Product", {
                "branche": str,
                "revision": str
            })]

    router = APIRouter(
        prefix="/productVersion",
        tags=["default"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = True
    enable_get_all = False
    enable_put = False
    enable_post = False
    enable_delete = False

class Reboot(AcicBackendGeneral):
    class Model(BaseModel):
        pass

    router = APIRouter(
        prefix="/reboot",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_put = True
    enable_post = False
    enable_delete = False

class Restart(AcicBackendGeneral):
    class Model(BaseModel):
        pass

    router = APIRouter(
        prefix="/restart",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_put = True
    enable_post = False
    enable_delete = False

class Route(AcicBackendGeneral):
    class Model(BaseModel):
        destination: str = "192.168.20.0"
        source: str = "192.168.20.1"
        gateway: str = "0.0.0.0"
        netmask: str = "255.255.255.0"
        metric: int = 101
        interface: str = "eth0"

    router = APIRouter(
        prefix="/route",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 6]

    enable_get_one = False
    enable_get_all = True
    enable_put = False
    enable_post = False
    enable_delete = False

class Snapshot(AcicBackendStreamGuid):
    class Model(BaseModel):
        pass

    router = APIRouter(
        prefix="/snapshot",
        tags=["streams"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]
    enable_post = False
    enable_delete = False
    enable_get_one = False
    enable_put = False


    @classmethod
    def create_routes(cls):
        @cls.router.get("/{streamid}")
        async def get(request: Request, streamid: int, width: int = 640, height: int = 480):
            uri = f"{cls.router.prefix}/{streamid}?width={width}&height={height}"
            content = cls.get_raw(uri)
            return StreamingResponse(content, media_type='image/jpeg', headers={"Content-Disposition": "attachment; filename=snapshot.jpg"})

class Sources(AcicBackendGeneral):
    class ModelStream(BaseModel):
        external_id: str

        class Config:
            extra = Extra.allow

    router = APIRouter(
        prefix="/source",
        tags=["streams"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = ModelStream
    version = [0, 1]

    @classmethod
    def create_routes(cls):
        @cls.router.get("s/{streamid}", response_model=cls.ModelStream)
        async def get(request: Request, streamid: int):
            uri = f"{cls.router.prefix}s/{streamid}"
            return cls.get(uri)

        @cls.router.put("s/{streamid}")
        async def put(request: Request, streamid: int, data: cls.ModelStream):
            uri = f"{cls.router.prefix}s/{streamid}"
            data = await request.json()
            return cls.put(uri, data)

        @cls.router.delete("s/{streamid}")
        async def delete(request: Request, streamid: int):
            uri = f"{cls.router.prefix}s/{streamid}"
            return cls.delete(uri)

        @cls.router.get("sDescription")
        async def get_description(request: Request):
            uri = f"{cls.router.prefix}sDescription"
            return cls.get(uri)

        @cls.router.get("Log/{streamid}")
        async def get_log(request: Request, streamid: int):
            uri = f"{cls.router.prefix}Log/{streamid}"
            res = cls.get_raw(uri)
            return StreamingResponse(res, media_type='text/plain')

class Streams(AcicBackendGeneralGuid):
    class Model(BaseModel):
        id: str = "1"
        source: str = "1"
        application: str = "MvActivityDetection"
        configuration: Optional[str] = "https://192.168.20.1:8080/ConfigTool.html?streamId=1&application=MvActivityDetection"

    router = APIRouter(
        prefix="/streams",
        tags=["streams"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 2]

    enable_get_all = False
    enable_get_all_list = True
    enable_post = True
    enable_delete = True
    enable_get_one = False
    enable_put = True

class Users(AcicBackendGeneralGuid):
    class Model(BaseModel):
        user: str = "administrator"
        password: Optional[str] = "ACIC"
        privileges: str = "Administrator"
        superUser: Optional[bool] = False

    router = APIRouter(
        prefix="/users",
        tags=["users"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_get_all_list = True
    enable_put = True
    enable_post = True
    enable_delete = True

class TestEvent(AcicBackendStreamGuid):
    class Model(BaseModel):
        pass

    router = APIRouter(
        prefix="/testEvent",
        tags=["streams"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 4]

    enable_get_one = False
    enable_get_all = False
    enable_get_all_list = False
    enable_post = True
    enable_delete = False
    enable_get_one = False
    enable_put = False

class Time(AcicBackendGeneral):
    class Model(BaseModel):
        utc: datetime.datetime
        timezone: str = "Europe/Paris"
    
    router = APIRouter(
        prefix="/time",
        tags=["settings"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 6]

    enable_get_one = True
    enable_get_all = False
    enable_put = True
    enable_post = False
    enable_delete = False

class Version(AcicBackendGeneral):
    class Model(BaseModel):
        major: int
        minor: int
        revision: int
    
    router = APIRouter(
        prefix="/version",
        tags=["default"],
        dependencies=[Depends(AcicAuthenticate)],
    )
    model = Model
    version = [0, 1]

    enable_get_one = True
    enable_get_all = False
    enable_put = False
    enable_post = False
    enable_delete = False

def get_routes():
    return [
        AdamServer.get_router(),
        AIService.get_router(),                 # erreur sur POST
        LPRService.get_router(),                # inexistant LPR
        AnalyticsRules.get_router(),
        Applications.get_router(),
        Authenticate.get_router(),
        Backup.get_router(),
        Restore.get_router(),                   # Pas de GET /restorePoints pour voir les guuid précédements envoyés.
                                                # Pour restore, il faut être maintainer, sauf qu'il faut être admin pour reboot.
        Calendar.get_router(),
        CameraFeatures.get_router(),
        CameraAnomaly.get_router(),
        DefaultGateway.get_router(),
        DiagnosticDump.get_router(),
        Doc.get_router(),
        ExacqServer.get_router(),
        Firmware.get_router(),                  # plz test
        FtpServer.get_router(),
        Halt.get_router(),
        HttpServer.get_router(),
        License.get_router(),
        LocalDns.get_router(),
        MetaDataRecorder.get_router(),
        ModbusServer.get_router(),
        Network.get_router(),
        Ntp.get_router(),
        Output.get_router(),
        Ping.get_router(),
        ProductVersion.get_router(),
        Reboot.get_router(),
        Restart.get_router(),                   # seems very slow
        Route.get_router(),                     # missing default route
        SmtpServer.get_router(),
        Snapshot.get_router(),
        Sources.get_router(),                   # SourcesDescription, SourceLog (sans s). Erreur du JSON, les string sont quoté 2x
        Streams.get_router(),
        TcpServer.get_router(),
        TestEvent.get_router(),
        Time.get_router(),
        Users.get_router(),                     # le changement de rôle oblige le changement de mot de passe.
        Version.get_router(),
    ]
