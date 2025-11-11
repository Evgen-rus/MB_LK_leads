"""
Файл: backend/app/schemas.py
Назначение: Pydantic-схемы ввода/вывода для API.
"""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


Day = Literal['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
ProjectStatus = Literal['Активен', 'На паузе']
DeliveryStatus = Literal['Активна', 'На модерации', 'Отключена']
CollectionSource = Literal['Сайты','Звонки','СМС','Ретросайты','Ретрозвонки','Пересечение']
DataSourceCode = Literal['B1','B2','B3','B4']


class CreateProjectItem(BaseModel):
    name: str
    tag: str
    collectionSource: CollectionSource
    dataSourceCode: DataSourceCode
    dataLimit: int
    status: ProjectStatus
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]


class CreateProjectsPayload(BaseModel):
    items: List[CreateProjectItem]


class ProjectUpdate(BaseModel):
    name: str
    tag: str
    status: ProjectStatus
    dataLimit: int
    regionMode: Optional[Literal['include','exclude']] = None
    regions: List[str] = []
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    days: List[Day]


class ProjectOut(BaseModel):
    id: int
    status: ProjectStatus
    deliveryStatus: DeliveryStatus
    name: str
    tag: str
    collectionSource: CollectionSource
    dataSourceCode: DataSourceCode
    regionMode: Optional[Literal['include','exclude']] = None
    regions: Optional[List[str]] = None
    sites: Optional[List[str]] = None
    phones: Optional[List[str]] = None
    smsSenderName: Optional[str] = None
    dataLimit: int
    numbersToday: int
    numbersTotal: int
    daysReceived: str
    sourcesCount: int
    createdAt: str

    class Config:
        from_attributes = True


class ProjectListOut(BaseModel):
    items: List[ProjectOut]
    total: int


class ClientErrorIn(BaseModel):
    message: str = Field(..., description="Сообщение ошибки")
    stack: Optional[str] = Field(None, description="Стек ошибки")
    url: Optional[str] = Field(None, description="URL страницы")
    userAgent: Optional[str] = Field(None, description="User-Agent браузера")
    level: Optional[Literal['error','warn','info']] = 'error'
    time: Optional[str] = None



class LeadOut(BaseModel):
    ext_id: int
    project_id: int
    created_at: str
    phone: str
    utm_campaign: Optional[str] = None

    class Config:
        from_attributes = True


class LeadsListOut(BaseModel):
    items: List[LeadOut]
    total: int


# -------- Черный список --------
class BlacklistPhoneOut(BaseModel):
    id: int
    phone: str
    createdAt: str

    class Config:
        from_attributes = True


class BlacklistAddIn(BaseModel):
    phones: List[str]


class BlacklistListOut(BaseModel):
    items: List[BlacklistPhoneOut]
    total: int