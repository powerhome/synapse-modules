"""Database helper methods"""
from synapse.logging import logging

logger = logging.getLogger()


class Helpers:
    """Utility class for common database functionality

    Here is an example in a class on how to call a helper defined
    here:

    ```python
    from sqlalchemy.orm import Session

    from ..models import User
    from ..helpers import Helpers

    helper = Helpers()


    class Foo:
        def __init__(self, config):
            self.engine = Setup.create_engine(
                user="postgres",
                password="postgres_password",
                db="synapse"
            )

        def query_user(username: string):
            with Session(self.engine) as session:
              helper.find_or_create_by(
                session,
                User,
                username="username"
                password="password"
                through=["username"]
              )
    ```
    """  # noqa: E501, RST214, RST215, RST301, RST201, RST203

    @staticmethod
    def find_or_create_by(session, model, **kwargs):
        """Similar to the ActiveRecord method of the same name

        Args:
            session:
                an engine's session object, usually called with
                Session(engine)
            model:
                the model class to query
            kwargs:
                the model values along with the kwarg `through` which is a
                string list of values to query

        Returns:
            a single instance of the class that has been created or found
        """
        model_dict = {}
        filter_terms = {}
        for key, value in kwargs.items():
            if key == "through":
                for term in value:
                    filter_terms[term] = kwargs.get(term)
            else:
                model_dict[key] = value

        instance = session.query(model).filter_by(**filter_terms).first()
        if instance:
            return instance
        else:
            instance = model(**model_dict)
            session.add(instance)
            session.commit()
            return instance
