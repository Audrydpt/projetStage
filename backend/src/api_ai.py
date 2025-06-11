from enum import Enum
import random
import cv2
import asyncio
import time
import numpy as np
import requests

from fastapi import APIRouter, UploadFile, Request
from fastapi.responses import Response

from service_ai import ServiceAI

class AIServiceAPI:
    def __init__(self, host: str = "127.0.0.1", port: int = 53211, version: list[int] = [1, 5, 0], naming_override=""):
        self.host = host
        self.port = port
        self.version = version
        self.queue = asyncio.Queue()

        name = "ai" if self.version[0] >= 2 else "numbering"
        if naming_override:
            name = naming_override

        self.app = APIRouter(
            prefix= f"/{name}",
            tags=["ai"]
        )
        self.__create_routes()
    
    def __create_routes(self):

        @self.app.get("/networks", include_in_schema=self.version[0] < 2)
        @self.app.get("/models", include_in_schema=self.version[0] >= 2)
        async def get_models(request: Request):
            uri = "models" if self.version[0] >= 2 else "networks"
            return requests.get(f"http://{self.host}:{self.port}/{uri}").json()


        @self.app.put("/networks", include_in_schema=self.version[0] < 2)
        @self.app.put("/models", include_in_schema=self.version[0] >= 2)
        async def put_models(request: Request, model: UploadFile):
            uri = "models" if self.version[0] >= 2 else "networks"
            content = await model.read()
            data = requests.put(f"http://{self.host}:{self.port}/{uri}",
                    files={"upload": (model.filename, content, model.content_type)}
            )
            return data.text


        @self.app.delete("/networks/{name}", include_in_schema=self.version[0] < 2)
        @self.app.delete("/models/{name}", include_in_schema=self.version[0] >= 2)
        async def delete_models(request: Request, name: str):
            uri = "models" if self.version[0] >= 2 else "networks"
            return requests.delete(f"http://{self.host}:{self.port}/{uri}/{name}")

        @self.app.get("/describe", include_in_schema=self.version[0] < 2)
        @self.app.get("/networks", include_in_schema=self.version[0] >= 2)
        async def describe(request: Request):
            uri = "networks" if self.version[0] >= 2 else "describe"
            return requests.get(f"http://{self.host}:{self.port}/{uri}").json()


        if self.version[0] == 2 and self.version[1] >= 2 or self.version[0] >= 3:
            @self.app.post("/networks/{model}", include_in_schema=(self.version[0] >= 2))
            async def process(request: Request,
                            image: UploadFile, model: str,
                            confidenceThreshold: float = 0.15, overlapThreshold: float = 0.3):
                
                uri = f"/{model}"
                bytes = await image.read()
                np_img = np.frombuffer(bytes, np.uint8)
                frame = cv2.imdecode(np_img, cv2.IMREAD_UNCHANGED)

                # Si l'image a un canal alpha (4 canaux), convertir en BGR
                if frame is not None and frame.shape[-1] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                elif frame is not None and len(frame.shape) == 2:
                    # Image en niveaux de gris, convertir en BGR
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                async with ServiceAI(self.host, self.port, uri, uri, uri) as client:
                    data = await client.send(frame, confidenceThreshold, overlapThreshold)
                    return data
                return None

            @self.app.post("/networks/{model}/demo", include_in_schema=(self.version[0] >= 2))
            async def process(request: Request,
                            image: UploadFile, model: str,
                            confidenceThreshold: float = 0.15, overlapThreshold: float = 0.3):
                
                uri = f"/{model}"
                bytes = await image.read()
                np_img = np.frombuffer(bytes, np.uint8)
                frame = cv2.imdecode(np_img, cv2.IMREAD_UNCHANGED)

                # Si l'image a un canal alpha (4 canaux), convertir en BGR
                if frame is not None and frame.shape[-1] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                elif frame is not None and len(frame.shape) == 2:
                    # Image en niveaux de gris, convertir en BGR
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

                async with ServiceAI(self.host, self.port, uri, uri, uri) as client:
                    data = await client.detect(frame)
                    for obj in data:
                        bbox = client.get_pixel_bbox(frame, obj)
                        prob = obj["bbox"]["probabilities"]
                        name = list(prob.keys())[0]
                        conf = prob[name]
                        
                        if conf > confidenceThreshold:
                            top, bottom, left, right = bbox
                            cv2.rectangle(frame, (left, top), (right, bottom), (0, 0, 255), 2)
                            cv2.putText(frame, name, (left, top), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
                            
                    return Response(content=cv2.imencode(".jpg", frame)[1].tobytes(), media_type="image/jpeg")
                    
                return None
        
        else:
            @self.app.post("/callback", include_in_schema=True)
            async def callback(request: Request):
                data = await request.json()
                uuid = data["uuid"]

                await self.queue.put((uuid, data))
                return True
            
            @self.app.post("/{model}", include_in_schema=self.version[0] < 2)
            @self.app.post("/networks/{model}", include_in_schema=(self.version[0] >= 2))
            async def process(request: Request,
                            image: UploadFile, model: str,
                            confidenceThreshold: float = 0.15, overlapThreshold: float = 0.3,
                            bbox: bool = True, debug: bool = True):
                data = await do_process(request, image, model, confidenceThreshold, overlapThreshold, bbox, debug)
                return data


            @self.app.post("/{model}/demo", include_in_schema=self.version[0] < 2)
            @self.app.post("/networks/{model}/demo", include_in_schema=(self.version[0] >= 2))
            async def process(request: Request,
                            image: UploadFile, model: str,
                            confidenceThreshold: float = 0.15, overlapThreshold: float = 0.3):
                data = await do_process(request, image, model, confidenceThreshold, overlapThreshold, True, True)

                try:
                    uri = data["srcImageProcessedUri"]
                    image = requests.get(uri).content
                    return Response(content=image, media_type="image/jpeg")
                except:
                    pass

                return data


            async def do_process(request: Request,
                            image: UploadFile, model: str,
                            confidenceThreshold: float = 0.15, overlapThreshold: float = 0.3,
                            bbox: bool = True, debug: bool = True):
                uuid = random.randint(100000000, 999999999)

                bytes = await image.read()
                np_img = np.frombuffer(bytes, np.uint8)
                frame = cv2.imdecode(np_img, cv2.IMREAD_UNCHANGED)

                # Si l'image a un canal alpha (4 canaux), convertir en BGR
                if frame is not None and frame.shape[-1] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                elif frame is not None and len(frame.shape) == 2:
                    # Image en niveaux de gris, convertir en BGR
                    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                
                cv2.imwrite(f"/tmp/{uuid}.jpg", frame)

                url = f"http://{self.host}:{self.port}/{model}"
                if self.version[0] >= 2:
                    url = f"http://{self.host}:{self.port}/networks/{model}"
                
                # Build callback path by replacing the last two segments with '/callback'
                path_parts = request.url.path.rstrip('/').split('/')
                if len(path_parts) >= 2:
                    callback_path = '/'.join(path_parts[:-2] + ['callback'])
                else:
                    callback_path = '/callback'
                callback_url = str(request.base_url).rstrip('/') + callback_path

                query = requests.post(
                    url=url,
                    json={
                        "image": f"{request.base_url}images/{uuid}.jpg",
                        "callback": callback_url,
                        "confidenceThreshold": confidenceThreshold,
                        "overlapThreshold": overlapThreshold,
                        "bbox": bbox,
                        "debug": debug,
                        "userDefinedParameters": {
                            "uuid": uuid
                        }
                    }
                )

                if query.status_code == 202:
                    # wait for callback
                    while True:
                        candidate_uuid, data = await asyncio.wait_for(self.queue.get(), 20)
                        if str(candidate_uuid) != str(uuid):
                            await self.queue.put((candidate_uuid, data))
                            time.sleep(0.1)
                        else:
                            break

                    # fix known issues:
                    if "srcImageProcessedUri" in data:
                        protocol = data["srcImageProcessedUri"].split(":")[0]
                        host = data["srcImageProcessedUri"].split(":")[1].replace("/", "")
                        port = data["srcImageProcessedUri"].split(":")[2].split("/")[0]
                        uri = data["srcImageProcessedUri"].split(":")[2].split("/")[1]

                        data["srcImageProcessedUri"] = f"{protocol}://{self.host}:{self.port}/{uri}"


                    return data

                return query.reason


        if self.version[0] >= 2:
            class LogKind(str, Enum):
                system = "system"
                result = "result"
                process = "process"
                model = "ModelOptimizer"

            @self.app.get("/logs/{kind}")
            async def log(kind: LogKind = LogKind.system):
                return requests.get(f"http://{self.host}:{self.port}/logs/{kind}").text.split("\n")[-1000:]
        else:
            @self.app.get("/systemlog")
            @self.app.get("/resultslog")
            @self.app.get("/processlog")
            async def log(request: Request):
                kind = request.url.path.split("/")[-1]
                return requests.get(f"http://{self.host}:{self.port}/{kind}").text.split("\n")[-1000:]


        if self.version[0] >= 2:
            class TopFormat(str, Enum):
                json = "json"
                txt = "txt"

            class DurationFormat(str, Enum):
                sec = "sec"
                hour = "hour"

            @self.app.get("/top")
            async def top(fmt: TopFormat, d: DurationFormat = DurationFormat.sec):
                data = requests.get(f"http://{self.host}:{self.port}/top?fmt={fmt.value}&d={d.value}")
                # sec, min, hour
                return Response(content=data.text, media_type=data.headers["Content-Type"])
        else:
            @self.app.get("/top")
            async def top():
                return requests.get(f"http://{self.host}:{self.port}/top").text


        if self.version[0] >= 2:
            @self.app.get("/optimizer")
            async def optimizer():
                return requests.get(f"http://{self.host}:{self.port}/optimizer").json()

def get_routes():
    numbernig = AIServiceAPI()
    lpr = AIServiceAPI(port=53212, naming_override="lpr")
    ai = AIServiceAPI(version=[2, 0, 0])
    return [numbernig.app, lpr.app, ai.app]