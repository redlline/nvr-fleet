from datetime import datetime
from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, BigInteger
from sqlalchemy.orm import relationship
from database import Base
import uuid


def gen_id():
    return str(uuid.uuid4())[:8]


class Site(Base):
    __tablename__ = "sites"
    id            = Column(String, primary_key=True, default=gen_id)
    name          = Column(String, nullable=False)
    city          = Column(String, default="")
    lat           = Column(Float, default=0.0)
    lon           = Column(Float, default=0.0)
    nvr_vendor    = Column(String, default="hikvision")
    nvr_ip        = Column(String, nullable=False)
    nvr_http_port = Column(Integer, default=80)
    nvr_control_port = Column(Integer, default=8000)
    nvr_user      = Column(String, default="admin")
    nvr_pass      = Column(String, default="")
    nvr_port      = Column(Integer, default=554)
    tunnel_http_port = Column(Integer, nullable=True)
    tunnel_control_port = Column(Integer, nullable=True)
    tunnel_rtsp_port = Column(Integer, nullable=True)
    channel_count = Column(Integer, default=16)
    stream_type   = Column(String, default="main")  # main | sub
    created_at    = Column(DateTime, default=datetime.utcnow)
    cameras       = relationship("Camera", back_populates="site", cascade="all, delete")
    agent         = relationship("Agent",  back_populates="site", cascade="all, delete", uselist=False)


class Camera(Base):
    __tablename__ = "cameras"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    site_id     = Column(String, ForeignKey("sites.id"), nullable=False)
    name        = Column(String, default="")
    channel     = Column(Integer, nullable=False)   # 1-based
    channel_id  = Column(Integer, nullable=False)   # 101, 201 ... for main; 102,202 for sub
    source_ref  = Column(String, nullable=True)
    profile_ref = Column(String, nullable=True)
    stream_type = Column(String, default="main")
    enabled     = Column(Boolean, default=True)
    site        = relationship("Site", back_populates="cameras")


class Agent(Base):
    __tablename__ = "agents"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    site_id   = Column(String, ForeignKey("sites.id"), unique=True, nullable=False)
    token     = Column(String, nullable=False)
    online    = Column(Boolean, default=False)
    last_seen = Column(DateTime, nullable=True)
    version   = Column(String, default="")
    uptime    = Column(Integer, default=0)
    site      = relationship("Site", back_populates="agent")


class StreamStat(Base):
    __tablename__ = "stream_stats"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    site_id     = Column(String, ForeignKey("sites.id"), nullable=False)
    stream_path = Column(String, nullable=False)
    ready       = Column(Boolean, default=False)
    updated     = Column(DateTime, default=datetime.utcnow)


class TrafficSample(Base):
    __tablename__ = "traffic_samples"
    id          = Column(Integer, primary_key=True, autoincrement=True)
    site_id     = Column(String, ForeignKey("sites.id"), nullable=False)
    stream_path = Column(String, nullable=False)
    rx_bytes    = Column(BigInteger, default=0)
    tx_bytes    = Column(BigInteger, default=0)
    ts          = Column(DateTime, default=datetime.utcnow)
