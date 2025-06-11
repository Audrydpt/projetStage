import os
import traceback
import av
import cv2
import json
import uuid
from fastapi.websockets import WebSocketState
import gunicorn
import httpx
import requests
import uvicorn
import datetime
import time
import aiohttp
import threading
import asyncio
import logging
import numpy as np
import io
from pydantic import Field, create_model
from dotenv import load_dotenv

from sqlalchemy import func, JSON, text
from sqlalchemy.inspection import inspect

from fastapi.staticfiles import StaticFiles
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Response, WebSocket, WebSocketDisconnect, HTTPException
from starlette.requests import Request

from gunicorn.app.base import BaseApplication

from task_manager import JobResult, ResultsStore, JobStatus, TaskManager

from typing import Annotated, Literal, Optional, Type, Union, List, Dict, Any
from enum import Enum

from swagger import get_custom_swagger_ui_html
from event_grabber import EventGrabber
from database import Dashboard, GenericDAL, Widget, Settings
from database import AcicAllInOneEvent, AcicCounting, AcicEvent, AcicLicensePlate, AcicNumbering, AcicOCR, AcicOccupancy, AcicUnattendedItem

from pydantic import BaseModel, Field, Extra

from vms import CameraClient
from api_cpp import get_routes as get_cpp_routes
from api_ai import get_routes as get_ai_routes
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(f"/tmp/{__name__}.log")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

class ModelName(str, Enum):
    minute = "1 minute"
    quarter = "15 minutes"
    half = "30 minutes"
    hour = "1 hour"
    day = "1 day"
    week = "1 week"
    month = "1 month"
    trimester = "3 months"
    semester = "6 months"
    year = "1 year"
    lifetime = "100 years"

load_dotenv()
FORENSIC_PAGINATION_ITEMS = int(os.getenv("FORENSIC_PAGINATION_ITEMS", "12"))

# Please follow: https://www.belgif.be/specification/rest/api-guide/#resource-uri
class FastAPIServer:
    def __init__(self, event_grabber:EventGrabber):
        self.event_grabber = event_grabber
        self.app = FastAPI(
            docs_url=None, redoc_url=None,
            swagger_ui_parameters={
                "deepLinking": True,
                "displayRequestDuration": True,
                "docExpansion": "none",
                "operationsSorter": "alpha",
                "persistAuthorization": True,
                "tagsSorter": "alpha",
            },
            servers=[
                {"description": "production", "url": "/front-api"},
                {"description": "development", "url": "/"},
            ])

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        if os.path.exists("/backend/assets"):
            self.app.mount("/static", StaticFiles(directory="/backend/assets/"), name="static")
        else:
            os.makedirs("assets", exist_ok=True)
            self.app.mount("/static", StaticFiles(directory="assets/"), name="static")

        self.__registered_dashboard = {}
        self.__define_endpoints()

    def __define_endpoints(self):

        @self.app.get("/", include_in_schema=False)
        async def custom_swagger_ui_html():
            return get_custom_swagger_ui_html(
                openapi_url="openapi.json",
                title=self.app.title + " - Swagger UI",
                swagger_ui_parameters=self.app.swagger_ui_parameters
            )

        @self.app.get("/servers/grabbers", tags=["servers"])
        async def get_all_servers(health: Optional[bool] = False):
            try:
                ret = []

                for grabber in self.event_grabber.get_grabbers():
                    status = dict()
                    status["type"] = grabber.hosttype
                    status["host"] = grabber.acichost
                    status["ports"] = {
                        "http": grabber.acicaliveport,
                        "https": 443,
                        "streaming": grabber.acichostport
                    }

                    if health:
                        status["health"] = {
                            "is_alive": grabber.is_alive(),
                            "is_longrunning": grabber.is_long_running(),
                            "is_reachable": grabber.is_reachable(timeout=0.2),
                            "is_streaming": grabber.is_streaming(timeout=0.2)
                        }

                    ret.append(status)

                return ret

            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))

        self.__create_endpoint("AcicUnattendedItem", AcicUnattendedItem)
        self.__create_endpoint("AcicCounting", AcicCounting)
        self.__create_endpoint("AcicNumbering", AcicNumbering, agg_func=func.avg(AcicNumbering.count))
        self.__create_endpoint("AcicOccupancy", AcicOccupancy, agg_func=func.avg(AcicOccupancy.value))
        self.__create_endpoint("AcicLicensePlate", AcicLicensePlate)
        self.__create_endpoint("AcicOCR", AcicOCR)
        self.__create_endpoint("AcicAllInOneEvent", AcicAllInOneEvent)
        self.__create_endpoint("AcicEvent", AcicEvent)
        self.__create_endpoint2()

        @self.app.get("/dashboard/widgets", tags=["dashboard"])
        async def get_dashboard():
            return self.__registered_dashboard

        self.__create_tabs()
        self.__create_widgets()
        self.__create_settings()
        self.__create_health()
        self.__create_forensic()
        self.__create_vms()
        self.__create_proxy()

    def __create_health(self):
        @self.app.get("/health", tags=["health"], include_in_schema=False)
        async def health():
            grabbers = {}
            for grabber in self.event_grabber.get_grabbers():
                grabbers[grabber.acichost] = "ok" if grabber.is_long_running() else "error"

            return {
                "api": "ok",
                "database": "ok",
                "grabbers": grabbers
            }

        @self.app.get("/health/aiServer/{ip}", tags=["health"])
        async def health_ai_server(ip: str):
            try:
                username = "administrator"
                password = "ACIC"

                async with aiohttp.ClientSession() as session:
                    # Premier appel pour obtenir l'adresse AI
                    ai_service_url = f"https://{ip}/api/aiService"
                    async with session.get(
                        ai_service_url, 
                        auth=aiohttp.BasicAuth(username, password),
                        headers={"Accept": "application/json"},
                        ssl=False,
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as response:
                        if response.status != 200:
                            return {"status": "error", "message": "Impossible to get AI IP"}
                        
                        ai_data = await response.json()
                        ai_ip = ai_data["address"]
                        ai_port = ai_data["port"]
                    
                    # Deuxième appel pour vérifier le service AI
                    describe_url = f"http://{ai_ip}:{ai_port}/describe"
                    async with session.get(
                        describe_url, 
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as describe_response:
                        if describe_response.status == 200:
                            return {"status": "ok"}
                        else:
                            return {"status": "error", "message": "AI service /describe endpoint not responding"}

            except aiohttp.ClientError as e:
                return {"status": "error", "message": f"Request failed: {str(e)}"}
            except asyncio.TimeoutError:
                return {"status": "error", "message": "Request timed out"}
        
        @self.app.get("/health/lprServer/{ip}", tags=["health"])
        async def health_lpr_server(ip: str):
            try:
                username = "administrator"
                password = "ACIC"

                async with aiohttp.ClientSession() as session:
                    # Premier appel pour obtenir l'adresse LPR
                    ai_service_url = f"https://{ip}/api/lprService"
                    async with session.get(
                        ai_service_url, 
                        auth=aiohttp.BasicAuth(username, password),
                        headers={"Accept": "application/json"},
                        ssl=False,
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as response:
                        if response.status != 200:
                            return {"status": "error", "message": "Impossible to get AI IP"}
                        
                        ai_data = await response.json()
                        ai_ip = ai_data["address"]
                        ai_port = ai_data["port"]
                    
                    # Deuxième appel pour vérifier le service LPR
                    describe_url = f"http://{ai_ip}:{ai_port}/describe"
                    async with session.get(
                        describe_url, 
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as describe_response:
                        if describe_response.status == 200:
                            return {"status": "ok"}
                        else:
                            return {"status": "error", "message": "LPR service /describe endpoint not responding"}

            except aiohttp.ClientError as e:
                return {"status": "error", "message": f"Request failed: {str(e)}"}
            except asyncio.TimeoutError:
                return {"status": "error", "message": "Request timed out"}

        @self.app.get("/health/secondaryServer/{ip}", tags=["health"])
        async def health_secondary_server(ip: str):
            try:
                async with aiohttp.ClientSession() as session:
                    describe_url = f"http://{ip}:8080/ConfigTool.html"
                    async with session.get(
                        describe_url, 
                        timeout=aiohttp.ClientTimeout(total=3)
                    ) as describe_response:
                        if describe_response.status == 401:
                            return {"status": "ok", "message": "Secondary server is reachable"}
                        else:
                            return {"status": "error", "message": f"Unexpected response: {describe_response.status}"}

            except asyncio.TimeoutError:
                return {"status": "error", "message": "Timeout"}
            except aiohttp.ClientConnectorError:
                return {"status": "error", "message": "Connection error"}
            except aiohttp.ClientError as e:
                return {"status": "error", "message": f"Request failed: {str(e)}"}

        @self.app.get("/health/worker", tags=["health"])
        async def test_worker():
            worker_pid = os.getpid()
            thread_id = threading.get_ident()
            
            # Simuler un traitement qui prend du temps, mais qui n'est pas bloquant
            start_time = time.time()
            await asyncio.sleep(5)  # Non-bloquant
            processing_time = time.time() - start_time
            
            return {
                "worker_pid": worker_pid,
                "thread_id": thread_id,
                "timestamp": time.time(),
                "processing_time": processing_time
            }
        
        @self.app.get("/health/worker/blocking", tags=["health"])
        def test_worker_blocking():            
            # Obtenir des informations sur le processus et le thread
            worker_pid = os.getpid()
            thread_id = threading.get_ident()
            
            # Simuler un traitement bloquant
            start_time = time.time()
            time.sleep(5)  # Bloquant
            processing_time = time.time() - start_time
            
            return {
                "worker_pid": worker_pid,
                "thread_id": thread_id,
                "timestamp": time.time(),
                "processing_time": processing_time,
                "type": "blocking"
            }

    def __create_forensic(self):
        
        class TimeRange(BaseModel):
            time_from: datetime.datetime
            time_to: datetime.datetime
        
        Confidence = Literal["low", "medium", "high"]
        Color = Literal["brown", "red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "white", "gray", "black"]

        class Model(BaseModel):
            sources: List[str]
            timerange: TimeRange
        
        class MMR(BaseModel):
            brand: str
            model: Optional[List[str]] = None
        
        class VehiculeApperance(BaseModel):
            type: Optional[List[str]] = None
            color: Optional[List[Color]] = None
            confidence: Confidence
        
        class VehiculeAttributes(BaseModel):
            mmr: Optional[List[MMR]] = None
            plate: Optional[str] = None
            other: Dict[str, bool]
            confidence: Confidence
        
        class ModelVehicle(Model):
            type: Literal["vehicle"]
            appearances: VehiculeApperance
            attributes: VehiculeAttributes
            context: Dict[str, Any]
        
        class Hair(BaseModel):
            length: Optional[List[Literal["none", "short", "medium", "long"]]] = None
            color: Optional[List[Literal["black", "brown", "blonde", "gray", "white", "other"]]] = None
            style: Optional[List[Literal["straight", "wavy", "curly"]]] = None

        class PersonApperance(BaseModel):
            gender: Optional[List[Literal["male", "female"]]] = None
            seenAge: Optional[List[Literal["child", "teen", "adult", "senior"]]] = None
            realAge: Optional[int] = None
            build: Optional[List[Literal["slim", "average", "athletic", "heavy"]]] = None
            height: Optional[List[Literal["short", "average", "tall"]]] = None
            hair: Hair
            confidence: Confidence
        
        class UpperPersonAttributes(BaseModel):
            color: Optional[List[Color]] = None
            type: Optional[List[str]] = None
        
        class LowerPersonAttributes(BaseModel):
            color: Optional[List[Color]] = None
            type: Optional[List[str]] = None
            
        class PersonAttributes(BaseModel):
            upper: UpperPersonAttributes
            lower: LowerPersonAttributes
            other: Dict[str, bool]
            confidence: Confidence

        class ModelPerson(Model):
            type: Literal["person"]
            appearances: PersonApperance
            attributes: PersonAttributes
            context: Dict[str, Any]
                
        @self.app.get("/forensics", tags=["forensics"])
        async def get_tasks():
            try:
                tasks = {}
                for job_id in await TaskManager.get_jobs():

                    metadata = await TaskManager.get_job_metadata(job_id)
                    status = TaskManager.get_job_status(job_id)

                    task_info = metadata if metadata else {}
                    task_info["status"] = status
                    task_info["progress"] = await TaskManager.get_job_progress(job_id)
                    task_info["count"] = await TaskManager.get_job_count(job_id)
                    task_info["total_pages"] = await TaskManager.get_job_total_pages(job_id)
                    
                    if status == JobStatus.FAILURE:
                        error, stacktrace = await TaskManager.get_job_error(job_id)
                        task_info["error"] = error
                        task_info["stacktrace"] = stacktrace
                        
                    tasks[job_id] = task_info
                    
                return {"tasks": tasks}
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des tâches: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.post("/forensics", tags=["forensics"])
        async def start_task(request: Request, data: Union[ModelVehicle, ModelPerson]):
            try:
                job_params = data.model_dump()
                
                # Soumettre la tâche appropriée selon le type
                if data.type == "vehicle":
                    job_id = TaskManager.submit_job("VehicleReplayJob", job_params)
                elif data.type == "person":
                    job_id = TaskManager.submit_job("PersonReplayJob", job_params)
                elif data.type == "mobility":
                    job_id = TaskManager.submit_job("MobilityReplayJob", job_params)
                else:
                    raise HTTPException(status_code=400, detail="Type non supporté")
                    
                return {"guid": job_id}
            
            except ValueError as e:
                logger.error(f"Erreur de validation: {e}")
                raise HTTPException(status_code=400, detail=str(e))
            except Exception as e:
                logger.error(f"Erreur lors du démarrage de la tâche: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=f"Erreur serveur: {str(e)}")
        
        @self.app.websocket("/forensics/{guid}")
        async def task_updates(websocket: WebSocket, guid: str):
            logger.info(f"start of task_updates")
            try:
                # Accepter la connexion WebSocket
                logger.info(f"Client connecté pour la tâche {guid}")
                await websocket.accept()
                logger.info(f"Client accepté pour la tâche {guid}")
                
                # Valider que la tâche existe
                status = TaskManager.get_job_status(guid)
                if not status:
                    await websocket.close(code=1000, reason="Tâche introuvable")
                    return
               
                # Utiliser la fonction de streaming du TaskManager
                logger.info(f"Démarrage du streaming pour la tâche {guid}")
                try:
                    await TaskManager.stream_job_results(websocket, guid)
                    logger.info(f"Streaming terminé pour la tâche {guid}")
                except WebSocketDisconnect:
                    logger.info(f"Client déconnecté pour la tâche {guid} (WebSocketDisconnect)")
                except asyncio.CancelledError:
                    logger.info(f"Streaming annulé pour la tâche {guid} (CancelledError)")
                except Exception as e:
                    logger.error(f"Erreur pendant le streaming de la tâche {guid}: {e}")
                    logger.error(traceback.format_exc())
                    if websocket.client_state != WebSocketState.DISCONNECTED:
                        logger.info(f"Envoi d'un message d'erreur pour la tâche {guid}")
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Erreur de streaming: {str(e)}"
                        })
                logger.info(f"Fin du streaming pour la tâche {guid}")

            except WebSocketDisconnect:
                logger.info(f"Client déconnecté pour la tâche {guid} (WebSocketDisconnect2)")
            
            except Exception as e:
                logger.error(f"Erreur WebSocket pour la tâche {guid}: {e}")
                logger.error(traceback.format_exc())
                
                # Tenter d'envoyer un message d'erreur avant de fermer
                try:
                    if websocket.client_state != WebSocketState.DISCONNECTED:
                        logger.info(f"Envoi d'un message d'erreur pour la tâche {guid}")
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Erreur: {str(e)}"
                        })
                        await websocket.close(code=1011, reason="Erreur serveur")
                except:
                    logger.info(f"Erreur lors de la fermeture de la connexion pour la tâche {guid}")
            
            finally:
                logger.info(f"Client déconnecté pour la tâche {guid}")
                # S'assurer que la connexion est fermée
                if websocket.client_state != WebSocketState.DISCONNECTED:
                    await websocket.close(code=1000, reason="Streaming terminé")
            
            logger.info(f"end of task_updates")

        @self.app.get("/forensics/{guid}", tags=["forensics"])
        async def get_task_status(guid: str):
            try:
                status = TaskManager.get_job_status(guid)
                if not status:
                    raise HTTPException(status_code=404, detail="Tâche introuvable")
                        
                return {
                    "guid": guid,
                    "status": status,
                }
            
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des résultats de la tâche {guid}: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.get("/forensics/{guid}/by-score", tags=["forensics"])
        async def get_results_sorted_by_score(guid: str, page: int = 1, desc: bool = True):
            try:
                # Calcul des indices de début et de fin pour la pagination
                start = (page - 1) * FORENSIC_PAGINATION_ITEMS
                end = page * FORENSIC_PAGINATION_ITEMS - 1

                # Récupérer les résultats triés par score
                results = await TaskManager.get_sorted_results(guid, sort_by="score", desc=desc, start=start, end=end)

                # Calculer le nombre total de pages
                total = await TaskManager.get_job_count(guid)
                total_pages = (total + FORENSIC_PAGINATION_ITEMS - 1) // FORENSIC_PAGINATION_ITEMS


                # Inclure le frame_uuid avec les métadonnées pour chaque résultat non final
                formatted_results = [
                    {**result.metadata, "frame_uuid": result.frame_uuid}
                    for result in results if not result.final
                ]

                return {
                    "results": formatted_results,
                    "pagination": {
                        "currentPage": page,
                        "totalPages": total_pages,
                        "pageSize": FORENSIC_PAGINATION_ITEMS,
                        "total": total,
                    }
                }
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des résultats triés par score: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/forensics/{guid}/by-date", tags=["forensics"])
        async def get_results_sorted_by_date(guid: str, page: int = 1, desc: bool = True):
            try:
                # Calcul des indices de début et de fin pour la pagination
                start = (page - 1) * FORENSIC_PAGINATION_ITEMS
                end = page * FORENSIC_PAGINATION_ITEMS - 1

                # Récupérer les résultats triés par date
                results = await TaskManager.get_sorted_results(guid, sort_by="date", desc=desc, start=start, end=end)

                # Calculer le nombre total de pages
                total = await TaskManager.get_job_count(guid)
                total_pages = (total + FORENSIC_PAGINATION_ITEMS - 1) // FORENSIC_PAGINATION_ITEMS

                # Inclure le frame_uuid avec les métadonnées pour chaque résultat non final
                formatted_results = [
                    {**result.metadata, "frame_uuid": result.frame_uuid}
                    for result in results if not result.final
                ]

                return {
                    "results": formatted_results,
                    "pagination": {
                        "currentPage": page,
                        "totalPages": total_pages,
                        "pageSize": FORENSIC_PAGINATION_ITEMS,
                        "total": total,
                    }
                }
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des résultats triés par date: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/forensics/{guid}/frames/{frameId}", tags=["forensics"])
        async def get_frame(guid: str, frameId: str):
            try:
                frame = await TaskManager.get_frame(guid, frameId)

                if frame is None:
                    raise HTTPException(status_code=404, detail="Frame not found")

                return Response(content=frame, status_code=200, headers={"Content-Type": "image/jpeg"})
            except Exception as e:
                logger.error(f"Erreur lors de la récupération de la frame {frameId} pour la tâche {guid}: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.delete("/forensics", tags=["forensics"])
        async def delete_all_forensic_task():
            """
            Supprime toutes les tâches forensiques et leurs résultats associés.
            """
            try:
                # Récupérer toutes les tâches
                tasks = await TaskManager.get_jobs()

                # Annuler toutes les tâches en cours
                cancelled_count = 0
                for job_id in tasks:
                    job_status = TaskManager.get_job_status(job_id)
                    if job_status in [JobStatus.PENDING, JobStatus.RECEIVED, JobStatus.STARTED]:
                        await TaskManager.cancel_job(job_id)
                        cancelled_count += 1

                # Supprimer toutes les données
                result = await TaskManager.delete_all_task_data()

                if result["success"]:
                    return {
                        "status": "ok",
                        "cancelled_tasks": cancelled_count
                    }
                else:
                    raise HTTPException(status_code=500, detail=result["message"])
            except Exception as e:
                logger.error(f"Erreur lors de la suppression de toutes les tâches: {e}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/forensics/{guid}", tags=["forensics"])
        async def delete_forensic_task(guid: str):
            """
            Supprime une tâche forensique et ses résultats associés.
            """
            try:
                # Vérifier si la tâche est en cours et l'annuler si nécessaire
                job_status = TaskManager.get_job_status(guid)

                if job_status in [JobStatus.PENDING, JobStatus.STARTED, JobStatus.RECEIVED]:
                    cancelled = await TaskManager.cancel_job(guid)
                    if not cancelled:
                        logger.warning(f"Impossible d'annuler la tâche {guid}, mais la suppression des données continuera")

                try:
                    result = await  TaskManager.delete_task_data(guid)
                    return result
                except ValueError as e:
                    raise HTTPException(status_code=404, detail="Tâche introuvable")

            except Exception as e:
                logger.error(f"Erreur lors de la suppression de la tâche {guid}: {e}")
                logger.error(traceback.format_exc())

    def __create_vms(self):
        async def get_vms_config():
            dal = GenericDAL()
            settings = await dal.async_get(Settings, key_index= "vms")
            if not settings or len(settings) != 1:
                raise Exception("VMS settings not found")
                
            settings_dict = settings[0].value_index
            vms_host = settings_dict.get("ip", None)
            vms_port = settings_dict.get("port", None)
            vms_username = settings_dict.get("username", None)
            vms_password = settings_dict.get("password", None)
            vms_type = settings_dict.get("type", None)
            
            return vms_host, vms_port, vms_username, vms_password, vms_type

        @self.app.post("/vms/test", tags=["vms"])
        async def test_vms(request: Request):
            try:
                data = await request.json()
                vms_host = data.get("ip", None)
                vms_port = data.get("port", None)
                vms_username = data.get("username", None)
                vms_password = data.get("password", None)
                vms_type = data.get("type", None)

                if vms_host is None or vms_port is None:
                    raise HTTPException(status_code=400, detail="VMS IP or port not configured. Please configure VMS settings before trying to access cameras.")
                
                VMS = CameraClient.create(vms_host, vms_port, vms_username, vms_password, vms_type)
                async with VMS() as client:
                    return await client.get_system_info()
            except Exception as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())
        
        @self.app.get("/vms/cameras", tags=["vms"])
        async def get_cameras():
            try:
                vms_host, vms_port, vms_username, vms_password, vms_type = await get_vms_config()
                if vms_host is None or vms_port is None:
                    raise HTTPException(status_code=400, detail="VMS IP or port not configured. Please configure VMS settings before trying to access cameras.")
                
                VMS = CameraClient.create(vms_host, vms_port, vms_username, vms_password, vms_type)
                async with VMS() as client:
                    return await client.get_system_info()
            except Exception as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.get("/vms/cameras/{guuid}/live", tags=["vms"])
        async def get_live(guuid: str):
            vms_host, vms_port, vms_username, vms_password, vms_type = await get_vms_config()
            if vms_host is None or vms_port is None:
                raise HTTPException(status_code=400, detail="VMS IP or port not configured. Please configure VMS settings before trying to access cameras.")
            
            try:
                VMS = CameraClient.create(vms_host, vms_port, vms_username, vms_password, vms_type)
                async with VMS() as client:
                    streams = client.start_live(guuid, True)
                    img, time_frame = await anext(streams)
                    _, bytes = cv2.imencode('.jpg', img)
                    return Response(content=bytes.tobytes(), status_code=200, headers={"Content-Type": "image/jpeg"})

            except Exception as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.get("/vms/cameras/{guuid}/replay", tags=["vms"])
        async def get_replay(guuid: str, from_time: datetime.datetime, to_time: datetime.datetime, gap: int = 0):
            try:
                vms_host, vms_port, vms_username, vms_password, vms_type = await get_vms_config()
                if vms_host is None or vms_port is None:
                    raise HTTPException(status_code=400, detail="VMS IP or port not configured. Please configure VMS settings before trying to access cameras.")
                
                VMS = CameraClient.create(vms_host, vms_port, vms_username, vms_password, vms_type)
                async with VMS() as client:                    
                    streams = client.start_replay(guuid, from_time.astimezone(datetime.timezone.utc), to_time.astimezone(datetime.timezone.utc), gap, False)
                    
                    container, codec = "webm", "vp8"
                    buffer = io.BytesIO()
                    output = av.open(buffer, mode='w', format=container)
                    stream = output.add_stream(codec, rate=1)
                    stream.pix_fmt = 'yuv420p'
                    #stream.width = 640
                    #stream.height = 480

                    async for img, time_frame in streams:
                        frame = av.VideoFrame.from_ndarray(img, format='bgr24')
                        packet = stream.encode(frame)
                        output.mux(packet)

                    output.close()
                    buffer.seek(0)

                    return Response(content=buffer.getvalue(), status_code=200, headers={"Content-Type": f"video/{container}"})
            except Exception as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

    def __create_tabs(self):
        Model = create_model('TabModel',
            title=(str, Field(description="The title of the dashboard tab")),
            order=(Optional[int], Field(default=None, description="The order of the tab"))
        )

        @self.app.get("/dashboard/tabs", tags=["dashboard/tabs"])
        async def get_tabs():
            try:
                ret = {}
                dal = GenericDAL()
                query = await dal.async_get(Dashboard, _order='order')
                for row in query:
                    data = {}
                    for column in inspect(Dashboard).mapper.column_attrs:
                        if column.key not in ['id', 'timestamp']:
                            data[column.key] = getattr(row, column.key)

                    ret[row.id] = data
                return ret

            except ValueError as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.post("/dashboard/tabs", tags=["dashboard/tabs"])
        async def add_tab(data: Model):
            try:
                dal = GenericDAL()
                tab = Dashboard(**data.dict(exclude_unset=True))
                guid = await dal.async_add(tab)
                tab.id = guid
                return tab
            except ValueError as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.put("/dashboard/tabs/{id}", tags=["dashboard/tabs"])
        async def update_tab(id: str, data: Model):
            try:
                dal = GenericDAL()
                obj = await dal.async_get(Dashboard, id=id)
                if obj is None or len(obj) != 1:
                    raise HTTPException(status_code=404, detail="Tab not found")

                for field, value in data.dict(exclude_unset=True).items():
                    setattr(obj[0], field, value)

                return await dal.async_update(obj[0])
            except ValueError as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.delete("/dashboard/tabs/{id}", tags=["dashboard/tabs"])
        async def delete_tab(id: str):
            try:
                dal = GenericDAL()
                tab = await dal.async_get(Dashboard, id=id)
                if tab is None or len(tab) != 1:
                    raise HTTPException(status_code=404, detail="Tab not found")
                
                widgets = await dal.async_get(Widget, dashboard_id=id)

                for widget in widgets:
                    await self.drop_materialized_view(str(widget.id))
                    await dal.async_remove(widget)

                return await dal.async_remove(tab[0])
            except ValueError as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

    async def create_materialized_view(
        self,
        widget_id: str,
        table: str,
        aggregation: str,
        group_by: Optional[str] = None,
        where: Optional[List[Dict[str, Any]]] = None
    ) -> bool:
        """Create a materialized view for a widget"""
        try:
            #Build the SQL query based on widget config
            if group_by:
                group_select = f", {group_by} AS {group_by}"
                group_by_clause = f"GROUP BY bucket, {group_by}"
            else:
                group_select = ""
                group_by_clause = "GROUP BY bucket"

            # Build the WHERE clause if provided
            if where:
                where_clause = f"WHERE "
                for q in where:
                    where_clause += f"{q['column']}::text = '{q['value']}' AND "
                where_clause = where_clause[:-4]
            else:
                where_clause = ""

            # Construct the SQL query including WHERE and GROUP BY clauses
            sql = f"""
            CREATE MATERIALIZED VIEW "widget_{widget_id}" WITH (timescaledb.continuous, timescaledb.materialized_only=false)  AS
            SELECT time_bucket('{aggregation}', timestamp, 'UTC') AS bucket,
                   count(timestamp) as counts
                   {group_select}
            FROM {table.lower()}
            {where_clause}
            {group_by_clause};
            """
            if aggregation == "1 minute" or aggregation == "15 minutes" or aggregation == "30 minutes" or aggregation == "1 hour":
                sql2 = f"""
                SELECT add_continuous_aggregate_policy('"widget_{widget_id}"'::regclass,
                start_offset => '1 day'::interval,
                end_offset => NULL,
                schedule_interval => '{aggregation}'::interval);
                """
            else:
                sql2 = f"""
                SELECT add_continuous_aggregate_policy('"widget_{widget_id}"'::regclass,
                start_offset => '1 day'::interval,
                end_offset => NULL
                schedule_interval => '1 hour'::interval);
                """

            # Execute the statement using SQLAlchemy
            dal = GenericDAL()
            await dal.async_raw(sql, without_transaction=True)
            await dal.async_raw(sql2, without_transaction=True)

            logger.info(f"Create materialized view {widget_id} with aggregation {aggregation} for widget {widget_id}")
            return True
        except Exception as e:
            logger.error(f'Failed to create materialized view for widget {widget_id}: {e}')
            logger.error(traceback.format_exc())
            return False
    
    async def drop_materialized_view(self, widget_id: str):
        """Drop a materialized view for a widget"""
        try:
            #Build the SQL query based on widget config
            sql = f"""
            DROP MATERIALIZED VIEW IF EXISTS "widget_{widget_id}";
            """
            # Execute the statement using SQLAlchemy
            dal = GenericDAL()
            await dal.async_raw(sql, without_transaction=True)
            logger.info(f"Drop materialized view {widget_id}")
            return True
        except Exception as e:
            logger.error(f'Failed to drop materialized view for widget {widget_id}: {e}')
            logger.error(traceback.format_exc())
            return False
        
    def __create_widgets(self):
        fields = {}
        for column in inspect(Widget).mapper.column_attrs:
            if column.key not in ['id', 'dashboard_id']:
                python_type = column.expression.type.python_type
                if isinstance(column.expression.type, JSON):
                    fields[column.key] = (Optional[Union[Dict[str, Any], List[Dict[str, Any]]]], Field(default=None))
                elif column.expression.nullable:
                    fields[column.key] = (Optional[python_type], Field(default=None, description=f"The {column.key} of the widget"))
                else:
                    fields[column.key] = (python_type, Field(description=f"The {column.key} of the widget") )

        Model = create_model('WidgetModel', **fields)

        @self.app.get("/dashboard/tabs/{id}/widgets", tags=["dashboard/tabs/widgets"])
        async def get_widgets(id: str):
            try:
                dal = GenericDAL()
                return await dal.async_get(Widget, dashboard_id=id, _order='order')

            except ValueError as e:
                raise HTTPException(status_code=500, detail=traceback.format_exc())

        @self.app.post("/dashboard/tabs/{id}/widgets", tags=["dashboard/tabs/widgets"])
        async def add_widget(id: str, data: Model):
            try:
                dal = GenericDAL()
                dashboard = await dal.async_get(Dashboard, id=id)
                if not dashboard or len(dashboard) != 1:
                    raise HTTPException(status_code=404, detail="Dashboard tab not found")

                widget = Widget(
                    dashboard_id=id,
                    **data.dict()
                )
                guid = await dal.async_add(widget)
                widget.id = guid

                # creation de la vue matérialisée
                await self.create_materialized_view(
                    str(guid),
                    widget.table,
                    widget.aggregation,
                    widget.groupBy if hasattr(widget, "groupBy") else None,
                    widget.where if hasattr(widget, "where") else None
                )

                return widget
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/dashboard/tabs/{id}/widgets", tags=["dashboard/tabs/widgets"])
        async def update_all_widgets(id: str, data: list[Model]):
            try:
                dal = GenericDAL()

                dashboard = await dal.async_get(Dashboard, id=id)
                if not dashboard or len(dashboard) != 1:
                    raise HTTPException(status_code=404, detail="Dashboard tab not found")

                current_widgets = await dal.async_get(Widget, dashboard_id=id)
                for widget in current_widgets:
                    await dal.async_remove(widget)

                for widget_data in data:
                    widget = Widget(
                        dashboard_id=id,
                        **widget_data.dict()
                    )
                    await dal.async_add(widget)

                return True

            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.patch("/dashboard/tabs/{id}/widgets", tags=["dashboard/tabs/widgets"])
        async def patch_all_widgets(id: str, data: list[dict]):
            try:
                dal = GenericDAL()

                # Verify dashboard exists
                dashboard = await dal.async_get(Dashboard, id=id)
                if not dashboard or len(dashboard) != 1:
                    raise HTTPException(status_code=404, detail="Dashboard tab not found")

                # Validate all widgets must have an id
                for widget_data in data:
                    if 'id' not in widget_data:
                        raise HTTPException(status_code=400, detail="Each widget must have an id")

                # Validate and prepare widgets
                widgets_to_update = []
                for widget_data in data:
                    widget_id = widget_data.pop('id')  # Remove id from update data

                    # Get existing widget
                    widget = await dal.async_get(Widget, id=widget_id)
                    if widget is None or len(widget) != 1:
                        raise HTTPException(status_code=404, detail=f"Widget {widget_id} not found")

                    widget = widget[0]
                    # Verify widget belongs to the specified dashboard
                    if str(widget.dashboard_id) != id:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Widget {widget_id} does not belong to specified dashboard"
                        )

                    # Update fields in memory
                    for field, value in widget_data.items():
                        if hasattr(widget, field):
                            setattr(widget, field, value)

                    widgets_to_update.append(widget)

                # All validations passed, perform updates
                return [await dal.async_update(widget) for widget in widgets_to_update]

            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/dashboard/tabs/{id}/widgets/{widget_id}", tags=["dashboard/tabs/widgets"])
        async def update_widget(id: str, widget_id: str, data: Model):
            try:
                dal = GenericDAL()

                widget = await dal.async_get(Widget, id=widget_id)
                if widget is None or len(widget) != 1:
                    raise HTTPException(status_code=404, detail="Widget not found")

                widget = widget[0]
                if str(widget.dashboard_id) != id:
                    raise HTTPException(status_code=400, detail="Widget does not belong to specified dashboard")

                for field, value in data.dict().items():
                    setattr(widget, field, value)

                res = await dal.async_update(widget)

                await self.drop_materialized_view(str(widget_id))
                await self.create_materialized_view(
                    str(widget_id),
                    widget.table,
                    widget.aggregation,
                    widget.groupBy if hasattr(widget, "groupBy") else None,
                    widget.where if hasattr(widget, "where") else None
                )
                return res

            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.delete("/dashboard/tabs/{id}/widgets/{widget_id}", tags=["dashboard/tabs/widgets"])
        async def delete_widget(id: str, widget_id: str):
            try:
                dal = GenericDAL()
                obj = await dal.async_get(Widget, id=widget_id)
                if obj is None or len(obj) != 1:
                    raise HTTPException(status_code=404, detail="Widget not found")
                
                await self.drop_materialized_view(str(widget_id))
                return await dal.async_remove(obj[0])
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))

    def __create_settings(self):
        @self.app.get("/settings", tags=["settings"])
        async def get_settings():
            try:
                dal = GenericDAL()
                settings = await dal.async_get(Settings)
                
                # Return the first settings object
                result = {}
                for setting in settings:
                    result[setting.key_index] = setting.value_index
                
                return result

            except ValueError as e:
                logger.error(f"Error retrieving dashboard settings: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/settings/{key}", tags=["settings"])
        async def get_settings_key(key: str):
            try:
                dal = GenericDAL()
                settings = await dal.async_get(Settings, key_index=key)
                if not settings or len(settings) != 1:
                    raise HTTPException(status_code=404, detail="Setting not found")
                
                return settings[0].value_index

            except ValueError as e:
                logger.error(f"Error retrieving dashboard settings: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.put("/settings/{key}", tags=["settings"])
        async def update_settings(request: Request, key: str):
            """Update a specific setting by key"""

            body = await request.json()
            try:
                dal = GenericDAL()
                settings = await dal.async_get(Settings, key_index=key)

                if not settings or len(settings) != 1:
                    raise HTTPException(status_code=404, detail="Setting not found")

                data = settings[0]
                data.value_index = body
                await dal.async_update(data)
                return data
                
            except ValueError as e:
                logger.error(f"Error updating setting '{key}': {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
        
    def __create_endpoint(self, path: str, model_class: Type, agg_func=None):
        query = {}
        for column in inspect(model_class).mapper.column_attrs:
            if column.key not in ['id', 'timestamp'] and 'url' not in column.key:
                python_type = column.expression.type.python_type
                query[column.key] = (Optional[Union[python_type, List[python_type]]], Field(default=None, description=f"Filter by {column.key}"))

                if path not in self.__registered_dashboard:
                    self.__registered_dashboard[path] = []
                self.__registered_dashboard[path].append(column.key)

        aggregate = (ModelName, Field(default=ModelName.hour, description="The time interval to aggregate the data"))
        group = (str, Field(default=None, description="The column to group by"))
        date = (datetime.datetime, Field(default=None, description="The timestamp to filter by"))
        
        AggregateParam = create_model(f"{model_class.__name__}Aggregate", aggregate=aggregate, group_by=group, time_from=date, time_to=date, **query)
        
        @self.app.get("/dashboard/widgets/" + path, tags=["dashboard"])
        async def get_bucket(kwargs: Annotated[AggregateParam, Query()]):
            try:
                time = kwargs.aggregate
                between = kwargs.time_from, kwargs.time_to
                group_by = kwargs.group_by.split(",") if kwargs.group_by is not None else None
                where = {k: v for k, v in kwargs if v is not None and k in query}

                dal = GenericDAL()
                aggregation = agg_func if agg_func is not None else func.count('*')
                
                cursor = await dal.async_get_bucket(model_class, _func=aggregation, _time=time, _group=group_by, _between=between, **where)

                ret = []
                for row in cursor:
                    data = {
                        "timestamp": row[0],
                        "count": row[1]
                    }
                    if group_by is not None:
                        for idx, group in enumerate(group_by):
                            data[group] = row[idx + 2]

                    ret.append(data)
                return ret
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))
    
    def __create_endpoint2(self, agg_func=None):
        query = {}

        aggregateMandatory = (ModelName, Field(description="The time interval to aggregate the data"))
        group = (str, Field(default=None, description="The column to group by"))
        date = (datetime.datetime, Field(default=None, description="The timestamp to filter by"))

        AggregateMandatoryParam = create_model(f"AggregateMandatory", aggregate=aggregateMandatory, group_by=group, time_from=date, time_to=date, **query)  
        AggregateParam = create_model(f"Aggregate", group_by=group, time_from=date, time_to=date, **query)    
        
        @self.app.get("/dashboard/tabs/{id}/widgets/{guid}", tags=["dashboard/tabs/widgets", "materialized"])
        async def get_bucket(id: str, guid: str, kwargs: Annotated[AggregateMandatoryParam, Query()]):
            try:
                if guid == "undefined":
                    return []
                
                time = kwargs.aggregate
                between = kwargs.time_from, kwargs.time_to
                group_by = kwargs.group_by.split(",") if kwargs.group_by is not None else None
                where = {k: v for k, v in kwargs if v is not None and k in query}
                matView = f"widget_{guid}"
                
                dal = GenericDAL()
                aggregation = agg_func if agg_func is not None else func.count('*')
                
                try:
                    cursor = await dal.async_get_view(matView, _time=time, _group=group_by, _between=between)
                except Exception as e:
                    logger.error(f"Error getting view {matView}: {e}")

                    widget = await dal.async_get(Widget, id=guid)
                    if not widget or len(widget) == 0:
                        return []
                        
                    widget = widget[0]
                    table_class = globals()[widget.table]
                    cursor = await dal.async_get_bucket(
                        table_class, 
                        _func=aggregation, 
                        _time=time, 
                        _group=group_by, 
                        _between=between,
                        **where
                    )

                ret = []
                for row in cursor:
                    data = {
                        "timestamp": row[0],
                        "count": row[1]
                    }
                    if group_by is not None:
                        for idx, group in enumerate(group_by):
                            data[group] = row[idx + 2]
                
                    ret.append(data)
                
                return ret
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))
    
        @self.app.get("/dashboard/tabs/{id}/widgets/{guid}/trends", tags=["dashboard/tabs/widgets/trends", "materialized"])
        async def get_trend(id: str, guid: str, kwargs: Annotated[AggregateParam, Query()]):
            try:
                if guid == "undefined":
                    return []
                
                between = kwargs.time_from, kwargs.time_to
                group_by = kwargs.group_by.split(",") if kwargs.group_by is not None else None
                matView = f"widget_{guid}"
                
                dal = GenericDAL()

                try:
                    trend_data = await dal.async_get_trend(view_name=matView, _between=between, _group=group_by)
                    
                    return trend_data
                except Exception as e:
                    raise HTTPException(status_code=500, detail=str(e))
                
            except Exception as e:
                logger.error(f"Error retrieving trend data for widget {guid}: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
            
        @self.app.get("/dashboard/tabs/{id}/widgets/{guid}/trends/{aggregate}", tags=["dashboard/tabs/widgets/trends", "materialized"])
        async def get_trend_aggregation(id: str, guid: str, aggregate: ModelName, kwargs: Annotated[AggregateParam, Query()]):
            try:
                if guid == "undefined":
                    return []
                
                between = kwargs.time_from, kwargs.time_to
                group_by = kwargs.group_by.split(",") if kwargs.group_by is not None else None
                matView = f"widget_{guid}"
                
                dal = GenericDAL()

                try:
                    trend_data_aggregate = await dal.async_get_trend_aggregate(
                        view_name=matView, 
                        _aggregate=aggregate, 
                        _group=group_by,
                        _between=between
                    )
                    
                    formatted_data = []
                    for row in trend_data_aggregate:
                        item = {
                            "bucket": row[0],
                            "avg": row[1],
                            "std": row[2],
                            "min": row[3],
                            "max": row[4]
                        }
                        
                        if group_by:
                            if isinstance(group_by, list):
                                for i, group_name in enumerate(group_by):
                                    item[group_name] = row[i + 5]
                            else:
                                item[group_by] = row[5]
                                
                        formatted_data.append(item)
                        
                    return formatted_data
                except Exception as e:
                    raise HTTPException(status_code=500, detail=str(e))
                
            except Exception as e:
                logger.error(f"Error retrieving trend data for widget {guid}: {str(e)}")
                raise HTTPException(status_code=500, detail=str(e))
            
    def __create_proxy(self):
        def add_routes(subpath, routes):
            for router in routes:
                original_tags = getattr(router, "tags", [])
                
                if subpath and original_tags:
                    new_tags = [f"{subpath[1:]}/{tag}" for tag in original_tags]
                    for route in router.routes:
                        route.tags = new_tags
                
                self.app.include_router(router, prefix=subpath)
        
        add_routes("/proxy/localhost", get_cpp_routes())
        add_routes("/proxy/localhost", get_ai_routes())

        @self.app.get("/proxy/{host}/{subpath:path}", tags=["proxy"])
        async def get_proxy(request: Request, host: str, subpath: str = ""):
            base_url = f"http://{host}"
            target_url = f"{base_url}/{subpath}" if subpath else base_url
            logger.info(f"GET Proxy to: {target_url} with params: {request.query_params}")
            
            # Préparer les en-têtes à transférer, exclure 'host'
            forward_headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
            
            async with httpx.AsyncClient(verify=False) as client:
                try:
                    response = await client.get(
                        target_url, 
                        params=request.query_params,
                        headers=forward_headers
                    )
                    return Response(
                        content=response.content, 
                        status_code=response.status_code, 
                        headers=dict(response.headers)
                    )
                except httpx.RequestError as exc:
                    logger.error(f"Proxy GET request failed: {exc}")
                    raise HTTPException(status_code=502, detail=f"Bad Gateway: {exc}")

        @self.app.post("/proxy/{host}/{subpath:path}", tags=["proxy"])
        async def post_proxy(request: Request, host: str, subpath: str = ""):
            base_url = f"http://{host}"
            target_url = f"{base_url}/{subpath}" if subpath else base_url
            logger.info(f"POST Proxy to: {target_url} with params: {request.query_params}")
            body = await request.body()
            forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'content-length']}
            
            async with httpx.AsyncClient(verify=False) as client:
                try:
                    response = await client.post(
                        target_url, 
                        content=body, 
                        params=request.query_params, 
                        headers=forward_headers
                    )
                    return Response(
                        content=response.content, 
                        status_code=response.status_code, 
                        headers=dict(response.headers)
                    )
                except httpx.RequestError as exc:
                    logger.error(f"Proxy POST request failed: {exc}")
                    raise HTTPException(status_code=502, detail=f"Bad Gateway: {exc}")

        @self.app.put("/proxy/{host}/{subpath:path}", tags=["proxy"])
        async def put_proxy(request: Request, host: str, subpath: str = ""):
            base_url = f"http://{host}"
            target_url = f"{base_url}/{subpath}" if subpath else base_url
            logger.info(f"PUT Proxy to: {target_url} with params: {request.query_params}")
            body = await request.body()
            forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'content-length']}

            async with httpx.AsyncClient(verify=False) as client:
                try:
                    response = await client.put(
                        target_url, 
                        content=body, 
                        params=request.query_params, 
                        headers=forward_headers
                    )
                    return Response(
                        content=response.content, 
                        status_code=response.status_code, 
                        headers=dict(response.headers)
                    )
                except httpx.RequestError as exc:
                    logger.error(f"Proxy PUT request failed: {exc}")
                    raise HTTPException(status_code=502, detail=f"Bad Gateway: {exc}")
        
        @self.app.delete("/proxy/{host}/{subpath:path}", tags=["proxy"])
        async def delete_proxy(request: Request, host: str, subpath: str = ""):
            base_url = f"http://{host}"
            target_url = f"{base_url}/{subpath}" if subpath else base_url
            logger.info(f"DELETE Proxy to: {target_url} with params: {request.query_params}")
            body = await request.body()
            forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in ['host', 'content-length']}

            async with httpx.AsyncClient(verify=False) as client:
                try:
                    response = await client.delete(
                        target_url, 
                        content=body if body else None, 
                        params=request.query_params, 
                        headers=forward_headers
                    )
                    return Response(
                        content=response.content, 
                        status_code=response.status_code, 
                        headers=dict(response.headers)
                    )
                except httpx.RequestError as exc:
                    logger.error(f"Proxy DELETE request failed: {exc}")
                    raise HTTPException(status_code=502, detail=f"Bad Gateway: {exc}")

    def start(self, host="0.0.0.0", port=5020):
        uvicorn.run(self.app, host=host, port=port, root_path="/front-api", ws_ping_interval=30, ws_ping_timeout=30)

class ThreadedFastAPIServer(BaseApplication):
    def __init__(self, event_grabber, threads=1, workers=2):
        self.api_server = FastAPIServer(event_grabber)
        self.options = {
            "workers": workers,
            "worker_connections": workers*256,
            "threads": threads,
            "worker_class": "uvicorn.workers.UvicornWorker",
            "preload_app": True,
            "accesslog": "-",
            "errorlog": "-",
            "loglevel": "info",
            "worker_options": {
                "ws_ping_interval": 30,
                "ws_ping_timeout": 30
            }
        }
        super().__init__()

    def load_config(self):
        for key, value in self.options.items():
            if key in self.cfg.settings and value is not None:
                self.cfg.set(key.lower(), value)

    def load(self):
        return self.api_server.app
    
    def start(self, host="0.0.0.0", port=5020):
        self.options["bind"] = f"{host}:{port}"
        if "root_path" not in self.options:
            self.options["root_path"] = "/front-api"

        self.load_config()
        self.run()
