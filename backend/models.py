from sqlalchemy import Column, Integer, String, Float, Date, Boolean, DateTime
from datetime import datetime
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
    account = Column(String, nullable=True)   # e.g. "Chase Checking", "Amex Blue Cash"
    notes = Column(String, nullable=True)     # user annotation

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
            "account": self.account,
            "notes": self.notes,
            "is_reviewed": self.is_reviewed,
        }


class BudgetTarget(Base):
    """
    Percentage-of-income target per category.
    'Savings' is a special pseudo-category meaning (income - expenses).
    target_amount = (percentage / 100) * monthly_income
    """
    __tablename__ = "budget_targets"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, nullable=False, unique=True, index=True)
    percentage = Column(Float, nullable=False)   # 0–100

    def to_dict(self):
        return {
            "id": self.id,
            "category": self.category,
            "percentage": self.percentage,
        }


class MerchantRule(Base):
    """User-confirmed merchant → category/subcategory mapping.
    Applied before AI on every upload, and retroactively to unreviewed transactions.
    """
    __tablename__ = "merchant_rules"

    id = Column(Integer, primary_key=True, index=True)
    merchant = Column(String, nullable=False, unique=True, index=True)
    category = Column(String, nullable=False)
    subcategory = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "merchant": self.merchant,
            "category": self.category,
            "subcategory": self.subcategory,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
