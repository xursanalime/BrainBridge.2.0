from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, Index, func, Boolean, Text
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

    words               = relationship("Word", back_populates="user", cascade="all, delete-orphan")
    sentence_progresses = relationship("SentenceProgress", back_populates="user", cascade="all, delete-orphan")


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

    user              = relationship("User", back_populates="words")
    sentence_progress = relationship("SentenceProgress", back_populates="word",
                                     uselist=False, cascade="all, delete-orphan")
    sentences         = relationship("UserSentence", back_populates="word", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "word", name="uq_user_word"),
        Index("ix_words_user_box",    "user_id", "box"),
        Index("ix_words_user_review", "user_id", "next_review"),
    )


class SentenceProgress(Base):
    """Tracks the Leitner box for sentence-writing per word per user."""
    __tablename__ = "sentence_progress"

    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False)
    word_id         = Column(Integer, ForeignKey("words.id"), nullable=False)
    sentence_box    = Column(Integer, default=1, nullable=False)   # 1-5
    sentences_done  = Column(Integer, default=0, nullable=False)   # 0,1,2 per session
    last_reviewed   = Column(DateTime(timezone=True), nullable=True)
    next_review     = Column(DateTime(timezone=True), server_default=func.now())
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="sentence_progresses")
    word = relationship("Word", back_populates="sentence_progress")

    __table_args__ = (
        UniqueConstraint("user_id", "word_id", name="uq_sentence_progress"),
        Index("ix_sp_user_box",    "user_id", "sentence_box"),
        Index("ix_sp_user_review", "user_id", "next_review"),
    )


class UserSentence(Base):
    """Stores every sentence a user has written for a word."""
    __tablename__ = "user_sentences"

    id              = Column(Integer, primary_key=True, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False)
    word_id         = Column(Integer, ForeignKey("words.id"), nullable=False)
    sentence_text   = Column(Text, nullable=False)
    is_correct      = Column(Boolean, nullable=False)
    ai_feedback     = Column(Text, nullable=True)    # JSON string
    sentence_number = Column(Integer, nullable=False)  # 1 or 2
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    word = relationship("Word", back_populates="sentences")
