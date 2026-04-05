from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index, func, Boolean
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id                 = Column(Integer, primary_key=True, index=True)
    email              = Column(String, unique=True, index=True, nullable=False)
    password_hash      = Column(String, nullable=False)
    prev_password_hash = Column(String, nullable=True)
    streak             = Column(Integer, default=0)
    last_study         = Column(DateTime(timezone=True), nullable=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    # brute-force protection
    failed_logins      = Column(Integer, default=0, nullable=False)
    locked_until       = Column(DateTime(timezone=True), nullable=True)
    locked_permanent   = Column(Boolean, default=False, nullable=False)
    # monthly password change limit
    pw_change_count    = Column(Integer, default=0, nullable=False)
    pw_change_month    = Column(String(7), nullable=True)   # "YYYY-MM"
    # password reset
    reset_token        = Column(String, nullable=True)
    reset_token_exp    = Column(DateTime(timezone=True), nullable=True)

    words = relationship("Word", back_populates="user", cascade="all, delete-orphan")


class OAuthState(Base):
    """Temporary OAuth state tokens stored in DB (needed for serverless/Vercel)."""
    __tablename__ = "oauth_states"

    state      = Column(String, primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Word(Base):
    __tablename__ = "words"

    id          = Column(Integer, primary_key=True, index=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    word        = Column(String, nullable=False)          # inglizcha
    translation = Column(String, nullable=False)          # o'zbekcha
    box         = Column(Integer, default=0, nullable=False)  # 0-5
    next_review = Column(DateTime(timezone=True), server_default=func.now())
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="words")

    __table_args__ = (
        UniqueConstraint("user_id", "word", name="uq_user_word"),
        Index("ix_words_user_box",    "user_id", "box"),
        Index("ix_words_user_review", "user_id", "next_review"),
    )
