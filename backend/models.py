from sqlalchemy import Column, Integer, String, Float, Date, Boolean
from backend.database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    merchant = Column(String, nullable=False)
    raw_desc = Column(String, nullable=False)
    category = Column(String, nullable=True, index=True)     # parent category
    subcategory = Column(String, nullable=True)               # sub-category
    amount = Column(Float, nullable=False)
    source_file = Column(String, nullable=True)

    # AI suggested; user sets is_reviewed=True after confirming
    is_reviewed = Column(Boolean, default=False, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date.isoformat() if self.date else None,
            "merchant": self.merchant,
            "raw_desc": self.raw_desc,
            "category": self.category,
            "subcategory": self.subcategory,
            "amount": self.amount,
            "source_file": self.source_file,
            "is_reviewed": self.is_reviewed,
        }
