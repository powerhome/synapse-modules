"""UserProfile model to extend the user data stored in matrix"""
from phonenumbers import PhoneNumberFormat, format_number, is_valid_number, parse
from sqlalchemy import Column, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import validates

Base = declarative_base()


class UserProfile(Base):
    """The user profile model"""

    __tablename__ = "user_profile"
    __table_args__ = {"schema": "connect"}

    user_id = Column(Text, primary_key=True)
    phone_number = Column(String(100))

    @validates("phone_number")
    def validate_name(self, key, phone_number_string):
        phone_number = parse(phone_number_string)
        valid = is_valid_number(phone_number)
        if not valid:
            raise Exception("phone_number %s not valid" % (phone_number_string))
        formatted = format_number(phone_number, PhoneNumberFormat.E164)
        return formatted
