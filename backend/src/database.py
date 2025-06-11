import os
import traceback
import threading
import uuid
import asyncio
import logging
from datetime import timedelta
from dotenv import load_dotenv
from functools import wraps
from typing import Type, Any, List, Dict, Union, Optional, TypeVar, Generic, Callable
import time

from sqlalchemy import JSON, column, create_engine, Column, Integer, REAL, String, Text, DateTime, DDL, PrimaryKeyConstraint, cast, func, inspect, text, literal, ForeignKey, select, delete
from sqlalchemy.dialects.postgresql import TIMESTAMP, INTEGER, UUID
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.autogenerate import produce_migrations
from alembic.operations.ops import ModifyTableOps, AlterColumnOp

from cron import Cron

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(f"/tmp/{__name__}.log")
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler())

def get_database_url(use_timescaledb=True, async_driver=False):
    load_dotenv()
    db_host = os.getenv("DB_HOST", "localhost")
    db_user = os.getenv("DB_USER", "postgres")
    db_pass = os.getenv("DB_PASS", "postgres")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "postgres")

    if use_timescaledb:
        prefix = 'timescaledb'
    else:
        prefix = 'postgresql'
    
    if async_driver:
        driver = 'asyncpg'
    else:
        driver = 'psycopg2'
    
    return f'{prefix}+{driver}://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}'

def run_async(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            raise RuntimeError(
                "Attempted to call an async function from a sync function while an event loop is running. "
                "This can lead to deadlocks. Consider restructuring your code to use async throughout, "
                "or use a separate thread for this operation."
            )
        else:
            return loop.run_until_complete(func(*args, **kwargs))
    return wrapper

class Base:
    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

Database = declarative_base(cls=Base)

class BaseEvent(Database):
    __abstract__ = True
    
    @declared_attr
    def __table_args__(cls):
        return (
            {
                'timescaledb_hypertable': {'time_column_name': 'timestamp'},
            },
        )
    
    host = Column(Text, index=True)
    stream_id = Column(Integer, index=True)
    timestamp = Column(TIMESTAMP(timezone=True), primary_key=True)

class AcicUnattendedItem(BaseEvent):
    roi_index = Column(Integer)
    url = Column(Text)
    person_url = Column(Text, nullable=True)

class AcicCounting(BaseEvent):
    line_index = Column(Text)
    direction = Column(Text)
    class_name = Column(Text)

class AcicNumbering(BaseEvent):
    roi_index = Column(Integer)
    count = Column(Integer)

class AcicOccupancy(BaseEvent):
    count = Column(REAL)            # Number of people
    value = Column(REAL)            # Percentage of occupancy

class AcicLicensePlate(BaseEvent):
    roi_index = Column(Integer)
    plate = Column(Text)
    country = Column(Text)
    text_color = Column(Text)
    background_color = Column(Text)
    foreground_color = Column(Text)

    # only available if MMR enabled:
    vehicle_brand = Column(Text, nullable=True)
    vehicle_type = Column(Text, nullable=True)
    vehicle_color = Column(Text, nullable=True)

    # image might be already deleted
    url = Column(Text, nullable=True)
    url_thumbnail = Column(Text, nullable=True)
    url_plate = Column(Text, nullable=True)

class AcicOCR(BaseEvent):
    roi_index = Column(Integer)
    text = Column(Text)
    type = Column(Text)
    checksum = Column(Text)

    # image might be already deleted
    url = Column(Text, nullable=True)
    url_thumbnail = Column(Text, nullable=True)
    url_plate = Column(Text, nullable=True)

class AcicAllInOneEvent(BaseEvent):
    class_name = Column(Text)
    event_name = Column(Text)
    public_name = Column(Text)
    event_type = Column(Text)
    rois = Column(Text, nullable=True)
    lines = Column(Text, nullable=True)

class AcicEvent(BaseEvent):
    name = Column(Text)
    state = Column(Text)


class Dashboard(Database):
    title = Column(Text)
    widgets = relationship("Widget", back_populates="dashboard")
    order = Column(Integer, default=0)

class Widget(Database):
    table = Column(Text)
    aggregation = Column(Text)
    duration = Column(Text)
    groupBy = Column(Text, nullable=True, default=None)
    where = Column(JSON, nullable=True, default=lambda: [])
    size = Column(Text)
    type = Column(Text)
    layout = Column(Text)
    title = Column(Text)
    order = Column(Integer, default=0)

    # Clé étrangère vers Dashboard
    dashboard_id = Column(UUID(as_uuid=True), ForeignKey('dashboard.id'), nullable=True)
    dashboard = relationship("Dashboard", back_populates="widgets")

class Settings(Database):
    key_index = Column(Text, nullable=False, index=True)
    value_index = Column(JSON, nullable=True, default=lambda: [])

class GenericDAL:
    initialized = False
    lock = threading.Lock()
    SyncEngine = None
    SyncSession = None
    AsyncEngine = None
    AsyncSession = None

    def __init__(self):
        if not GenericDAL.SyncSession or True:
            with GenericDAL.lock:
                GenericDAL.SyncEngine  = create_engine(
                    get_database_url(use_timescaledb=True, async_driver=False), 
                    echo=True, 
                    connect_args={'client_encoding': 'utf8'},
                    pool_size=3,
                    max_overflow=2,
                    pool_timeout=30,
                    pool_recycle=1*60*60
                )
                GenericDAL.SyncSession = sessionmaker(bind=GenericDAL.SyncEngine , expire_on_commit=False)
        
        if not GenericDAL.AsyncSession or True:
            with GenericDAL.lock:
                GenericDAL.AsyncEngine = create_async_engine(
                    get_database_url(use_timescaledb=True, async_driver=True), 
                    echo=True,
                    pool_size=5,
                    max_overflow=5,
                    pool_timeout=30,
                    pool_recycle=1*60*60
                )
                GenericDAL.AsyncSession = async_sessionmaker(bind=GenericDAL.AsyncEngine, expire_on_commit=False)
        
        if not GenericDAL.initialized:
            with GenericDAL.lock:
                if not GenericDAL.initialized:
                    GenericDAL.__update_schema(self)
                    GenericDAL.__seed_database(self)
                    GenericDAL.__init_cron(self)
                    GenericDAL.initialized = True
    
    def __update_schema(self):
        def add_using_clause(op):
            if isinstance(op.modify_type, Integer) and isinstance(op.existing_type, Text):
                using_clause = (f"COALESCE(NULLIF(REGEXP_REPLACE({op.column_name}, '[^0-9]', '', 'g'), ''), '0')::integer")
                return AlterColumnOp(
                    table_name=op.table_name,
                    column_name=op.column_name,
                    modify_type=op.modify_type,
                    existing_type=op.existing_type,
                    schema=op.schema,
                    existing_nullable=op.existing_nullable,
                    existing_server_default=op.existing_server_default,
                    existing_comment=op.existing_comment,
                    postgresql_using=using_clause
                )
            elif isinstance(op.modify_type, UUID) and isinstance(op.existing_type, Integer):
                using_clause = f"('00000000-0000-0000-0000-' || lpad(to_hex({op.column_name}), 12, '0'))::uuid"
                
                # Drop the existing default value
                drop_default_op = AlterColumnOp(
                    table_name=op.table_name,
                    column_name=op.column_name,
                    existing_type=op.existing_type,
                    existing_server_default=True,  # Set this to True to indicate an existing default
                    modify_server_default=None     # Set to None to drop the default
                )
                
                # Alter the column type with the using clause
                alter_type_op = AlterColumnOp(
                    table_name=op.table_name,
                    column_name=op.column_name,
                    existing_type=op.existing_type,
                    modify_type=op.modify_type,
                    postgresql_using=using_clause,
                )
                
                # Set the new default value
                set_default_op = AlterColumnOp(
                    table_name=op.table_name,
                    column_name=op.column_name,
                    existing_type=op.modify_type,
                    existing_server_default=None,
                    modify_server_default=text('gen_random_uuid()')
                )
                return [drop_default_op, alter_type_op, set_default_op]

            else:
                return op

        with GenericDAL.SyncSession() as session:
            session.execute(DDL("SET client_encoding TO 'UTF8'"))
            session.execute(DDL("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
            session.execute(DDL("CREATE EXTENSION IF NOT EXISTS timescaledb"))
            session.execute(DDL("CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit"))
            session.commit()
        
        print("Binding schema to engine...")
        Database.metadata.reflect(GenericDAL.SyncEngine)
        Database.metadata.create_all(GenericDAL.SyncEngine)

        print("Trying to update schema...")
        # got issue using the same engine, because of dialect timescaledb != postgresql
        with create_engine(get_database_url(use_timescaledb=False, async_driver=False), echo=False, connect_args={'client_encoding': 'utf8'}).connect() as conn:
            trans = conn.begin()
            try:
                # Configure the migration context with the connection
                context = MigrationContext.configure(conn)
                migrations = produce_migrations(context, Database.metadata)
                if not migrations.upgrade_ops.is_empty():

                    print("Updating schema...")

                    operations = Operations(context)

                    use_batch = GenericDAL.SyncEngine.name == "sqlite"
                    stack = [migrations.upgrade_ops]
                    while stack:
                        elem = stack.pop(0)
                        print(elem)

                        if use_batch and isinstance(elem, ModifyTableOps):
                            with operations.batch_alter_table(elem.table_name, schema=elem.schema) as batch_ops:
                                for table_elem in elem.ops:
                                    batch_ops.invoke(table_elem)
                        elif hasattr(elem, "ops"):
                            stack.extend(elem.ops)
                        else:
                            if isinstance(elem, AlterColumnOp):
                                elem = add_using_clause(elem)
                                if isinstance(elem, list):
                                    for op in elem:
                                        operations.invoke(op)
                                else:
                                    operations.invoke(elem)
                            else:
                                operations.invoke(elem)

                trans.commit()
            except Exception as e:
                trans.rollback()
                print(f"An error occurred during migration: {e}")
            print("Schema updated")
        
        print("Schema is ready")

    def __seed_database(self):
        with GenericDAL.SyncSession() as session:
            query = session.query(Dashboard).all()
            
            # Create a default dashboard if none exists
            if not query or len(query) == 0:
                dashboard = Dashboard(title="Main dashboard", order=0)
                session.add(dashboard)
                session.commit()
            
            # Update the order of the default dashboard
            if query and len(query) == 1 and not query[0].order:
                query[0].order = 0
                session.commit()
            
            # Create default settings if needed
            settings_retention = session.query(Settings).filter(Settings.key_index == "retention").first()
            if settings_retention is None:
                default_retention = Settings(key_index="retention", value_index={"days": "90"})
                session.add(default_retention)
                session.commit()

            settings_vms = session.query(Settings).filter(Settings.key_index == "vms").first()
            if settings_vms is None:
                default_vms = Settings(key_index="vms", value_index={"type": "", "ip": "", "port": "", "username": "", "password": ""})
                session.add(default_vms)
                session.commit()
            
            settings_ai = session.query(Settings).filter(Settings.key_index == "ai").first()
            if settings_ai is None:
                default_ai = Settings(key_index="ai", value_index={"ip": "", "port": "", "type": ""})
                session.add(default_ai)
                session.commit()

    def __init_cron(self):
        trigger = "0 0 * * *"

        event_classes = []
        for _, cls in Database.registry._class_registry.items():
            if isinstance(cls, type) and issubclass(cls, BaseEvent):
                if '__abstract__' not in cls.__dict__:
                    event_classes.append(cls)

        cron = Cron()
        for event_class in event_classes:
            cron.add_job(lambda cls=event_class: self.clean(cls), trigger)

    # ----- Synchronous API methods -----

    def add(self, obj) -> uuid.UUID:
        return run_async(self.async_add)(obj)
            
    def get(self, cls, _func=None, _group=None, _having=None, _order=None, **filters) -> List[Any]:
        return run_async(self.async_get)(cls, _func, _group, _having, _order, **filters)
    
    def get_bucket(self, cls, _func=None, _time="1 hour", _group=None, _having=None, _between=None, **filters) -> List[Any]:
        return run_async(self.async_get_bucket)(cls, _func, _time, _group, _having, _between, **filters)

    def update(self, obj) -> Any:
        return run_async(self.async_update)(obj)

    def remove(self, obj) -> bool:
        return run_async(self.async_remove)(obj)
    
    def clean(self, cls) -> bool:
        logger.info(f"Cleaning {cls}")

        with GenericDAL.SyncSession() as session:
            try:
                setting = session.query(Settings).filter(Settings.key_index == "retention").first()
                days = int(setting.value_index) if setting else 90
                cutoff_date = func.now() - timedelta(days=days)
                rows = 10000

                while True:
                    # Récupérer un lot d'ids à supprimer
                    subquery = session.query(cls.id).filter(cls.timestamp < cutoff_date).limit(rows).subquery()

                    # Supprimer les enregistrements dont les ids sont dans la sous-requête
                    stmt = delete(cls).where(cls.id.in_(subquery))
                    result = session.execute(stmt)
                    session.commit()

                    # Si aucun enregistrement n'a été supprimé, sortir de la boucle
                    if result.rowcount == 0:
                        break

                    # Petite pause entre les lots
                    time.sleep(0.1)

                logger.info(f"Completed cleaning {cls} - {days} days")
                session.commit()
                return True
            except Exception as e:
                logger.error(f"Error cleaning {cls}: {e} {traceback.format_exc()}")
                return False

        return False

    # ----- Asynchronous API methods -----

    async def async_add(self, obj) -> uuid.UUID:
        async with GenericDAL.AsyncSession() as session:
            session.add(obj)
            await session.commit()
            await session.refresh(obj)
            return obj.id

    async def async_get(self, cls, _func=None, _group=None, _having=None, _order=None, **filters) -> List[Any]:
        async with GenericDAL.AsyncSession() as session:
            # Remplacement de session.query(cls)
            if _func is not None:
                stmt = select(_func, _func.clause_expr)
            else:
                stmt = select(cls)
            
            # Conversion de filter_by en where
            if filters:
                conditions = [getattr(cls, key) == value for key, value in filters.items()]
                stmt = stmt.where(*conditions)
            
            if _group is not None:
                stmt = stmt.add_columns(_group)
                stmt = stmt.group_by(_group)
            
            if _having is not None:
                stmt = stmt.having(_having)
            
            if _order is not None:
                stmt = stmt.order_by(_order)
            
            result = await session.execute(stmt)
            # Si _func est utilisé, on renvoie le résultat complet (tuple), sinon les objets mappés
            if _func is None:
                result = result.scalars().all()
            else:
                result = result.all()
            return result
    
    async def async_get_bucket(self, cls, _func=None, _time="1 hour", _group=None, _having=None, _between=None, **filters) -> List[Any]:
        async with GenericDAL.AsyncSession() as session:
            # Remplacement de session.query(…)
            # Somehow, text(f"'{_time}'") get converted into ModelName.hour, instead of '1 hour'
            stmt = select(func.time_bucket_gapfill(text("'" + _time + "'"), cls.timestamp, 'UTC').label('_timestamp'))
    
            if _func is not None:
                stmt = stmt.add_columns(_func)
    
            if filters:
                conditions = [
                    getattr(cls, key).in_(value) if isinstance(value, list) else getattr(cls, key) == value
                    for key, value in filters.items()
                ]
                stmt = stmt.where(*conditions)
    
            if _between is not None and len(_between) == 2 and _between[0] is not None and _between[1] is not None:
                stmt = stmt.where(cls.timestamp >= _between[0], cls.timestamp < _between[1])
            
            # Pour group_by et order_by, on utilise column('_timestamp')
            stmt = stmt.group_by(column('_timestamp'))
            if _group is not None:
                if isinstance(_group, list):
                    for group in _group:
                        stmt = stmt.add_columns(column(group))
                        stmt = stmt.group_by(column(group))
                else:
                    stmt = stmt.add_columns(column(_group))
                    stmt = stmt.group_by(column(_group))
            
            if _having is not None:
                stmt = stmt.having(_having)
            
            stmt = stmt.order_by(column('_timestamp'))        
            if _group is not None:
                if isinstance(_group, list):
                    for group in _group:
                        stmt = stmt.order_by(column(group))
                else:
                    stmt = stmt.order_by(column(_group))    
          
            result = await session.execute(stmt)
            result = result.all()
            return result

    async def async_get_view(self, view_name, _time="1 hour", _group=None, _between=None) -> List[Any]:
        async with GenericDAL.AsyncSession() as session:
            # Start with a base select statement using time_bucket_gapfill
            bucket_column = column('bucket')
            stmt = select(func.time_bucket_gapfill(text("'" + _time + "'"), bucket_column, 'UTC').label('_timestamp'))
            stmt = stmt.add_columns(column('counts').label('count'))
                        
            # FROM clause
            # Need to use from_statement since we're working with a view
            stmt = stmt.select_from(text(f'"{view_name}"'))
            
            if _between is not None and len(_between) == 2 and _between[0] is not None and _between[1] is not None:
                stmt = stmt.where(bucket_column >= _between[0], bucket_column < _between[1])

            # GROUP BY clause is handled in the view, so we only add the columns
            if _group is not None:
                if isinstance(_group, list):
                    for group in _group:
                        stmt = stmt.add_columns(column(group))
                else:
                    stmt = stmt.add_columns(column(_group))
            
            
            # Order by timestamp
            stmt = stmt.order_by(column('_timestamp'))
            if _group is not None:
                if isinstance(_group, list):
                    for group in _group:
                        stmt = stmt.order_by(column(group))
                else:
                    stmt = stmt.order_by(column(_group))
            
            try:
                result = await session.execute(stmt)
                return result.all()
            except Exception as e:
                logger.error(f"Error executing view query: {e}")
                logger.error(f"Query was: {stmt}")
                raise

    async def async_get_trend(self, view_name, _between=None, _group=None, **filters) -> JSON:
        async with GenericDAL.AsyncSession() as session:
            #def des fonctions de calcul
            avg_func = func.avg(column('counts'))
            med_func = func.percentile_cont(0.5).within_group(column('counts'))
            std_func = func.stddev(column('counts'))
            pc5_func = func.percentile_cont(0.05).within_group(column('counts'))
            pc95_func = func.percentile_cont(0.95).within_group(column('counts'))
            min_func = func.min(column('counts'))
            max_func = func.max(column('counts'))

            #construction du json à renvoyer
            stats_obj = func.jsonb_build_object(
                'avg', avg_func,
                'med', med_func,
                'std', std_func,
                'pc5', pc5_func,
                'pc95', pc95_func,
                'min', min_func,
                'max', max_func
            ).label('stats')

            if _group is None:
                stmt = select(stats_obj).select_from(text(f'"{view_name}"'))
                if _between is not None and _between[0] is not None and _between[1] is not None:
                    stmt = stmt.where(column('bucket') >= _between[0], column('bucket') < _between[1])
                
                try:
                    result = await session.execute(stmt)
                    global_stats = { 'global': result.scalar_one_or_none()}
                    return global_stats
                except Exception as e:
                    logger.error(f"Error executing trend query: {e}")
                    logger.error(f"Query was: {stmt}")
                    raise
            else:
                if isinstance(_group, list):
                    group_cols = [column(g) for g in _group]
                    stmt = select(*group_cols, stats_obj)
                    for g in group_cols:
                        stmt = stmt.group_by(g)
                else:
                    group_col = column(_group)
                    stmt = select(group_col, stats_obj)
                    stmt = stmt.group_by(group_col)

                stmt = stmt.select_from(text(f'"{view_name}"'))
                stmt2 = select(stats_obj).select_from(text(f'"{view_name}"'))

                if _between is not None and _between[0] is not None and _between[1] is not None:
                    stmt = stmt.where(column('bucket') >= _between[0], column('bucket') < _between[1])
                    stmt2 = stmt2.where(column('bucket') >= _between[0], column('bucket') < _between[1])

                try:
                    result = await session.execute(stmt)
                    result2 = await session.execute(stmt2)
                    global_stats = result2.scalar_one_or_none()
                    
                    group_by_data = []
                    for row in result.all():
                        if isinstance(_group, list):
                            # Créer un dictionnaire pour chaque groupe
                            group_item = {}
                            for i, group_name in enumerate(_group):
                                group_item[group_name] = row[i]
                            group_item['stats'] = row[-1]  # Dernière colonne = stats
                        else:
                            # Un seul groupe
                            group_item = {
                                _group: row[0],
                                'stats': row[1]
                            }
                        group_by_data.append(group_item)

                    combined_result = {
                        'global': global_stats,
                        'group': group_by_data
                    }
                    return combined_result
                except Exception as e:
                    logger.error(f"Error executing trend query: {e}")
                    logger.error(f"Query was: {stmt}")
                    raise

    async def async_get_trend_aggregate(self, view_name,_aggregate, _group=None, _between=None) -> List[Any]:
        async with GenericDAL.AsyncSession() as session:
            #def des fonctions de calcul
            avg_func = func.avg(column('counts'))
            std_func = func.stddev(column('counts'))
            min_func = func.min(column('counts'))
            max_func = func.max(column('counts'))

            match _aggregate:
                case '1 minute':
                    extract_func = func.extract('minute',column('bucket')).label('time_bucket')
                case '15 minutes':
                    extract_func = func.extract('minute',column('bucket')).label('time_bucket')
                case '30 minutes':
                    extract_func = func.extract('minute',column('bucket')).label('time_bucket')
                case '1 hour':
                    extract_func = func.extract('hour',column('bucket')).label('time_bucket')
                case '1 day':
                    extract_func = func.extract('isodow',column('bucket')).label('time_bucket')
                case '1 week':
                    extract_func = func.extract('week',column('bucket')).label('time_bucket')
                case '1 month':
                    extract_func = func.extract('month',column('bucket')).label('time_bucket')
                case '3 months':
                    extract_func = func.extract('month',column('bucket')).label('time_bucket')
                case '6 months':
                    extract_func = func.extract('month',column('bucket')).label('time_bucket')
                case '1 year':
                    extract_func = func.extract('year',column('bucket')).label('time_bucket')
                case '100 years':
                    extract_func = func.extract('year',column('bucket')).label('time_bucket')

            date_trunc_expr = text("date_trunc('day', now() - interval '1 day')")

            stmt = select(extract_func, avg_func, std_func, min_func, max_func)
            stmt = stmt.select_from(text(f'"{view_name}"'))
            stmt = stmt.where(column('bucket') < date_trunc_expr)

            if _group is not None:
                if isinstance(_group, list):
                    for g in _group:
                        stmt = stmt.add_columns(column(g))
                        stmt = stmt.group_by(extract_func, column(g))
                else:
                    stmt = stmt.add_columns(column(_group))
                    stmt = stmt.group_by(extract_func, column(_group))
            else:
                stmt = stmt.group_by(column('time_bucket'))
            
            stmt = stmt.order_by(column('time_bucket'))
            
            try:
                result = await session.execute(stmt)
                return result.all()
            except Exception as e:
                logger.error(f"Error executing trend query: {e}")
                logger.error(f"Query was: {stmt}")
                raise

    async def async_update(self, obj) -> Any:
        async with GenericDAL.AsyncSession() as session:
            result = await session.merge(obj)
            await session.commit()
            return result

    async def async_remove(self, obj) -> bool:
        async with GenericDAL.AsyncSession() as session:
            await session.delete(obj)
            await session.commit()
            return True
    
    async def async_raw(self, sql: str, params: Optional[Dict[str, Any]] = None, without_transaction: bool = False) -> Any:

          if without_transaction:
              async with GenericDAL.AsyncEngine.connect() as conn:
                  conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                  result = await conn.execute(text(sql), params or {})
                  return result
          else:
              async with GenericDAL.AsyncSession() as session:
                  result = await session.execute(text(sql), params or {})
                  await session.commit()
                  return result

    