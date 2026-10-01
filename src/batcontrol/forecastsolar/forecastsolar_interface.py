""" Interface for solar forecast classes """

from abc import ABC, abstractmethod

class ForecastSolarInterface(ABC):
    """ Interface for SolarAPI classes """
    @abstractmethod
    def __init__(self, pvinstallations, timezone, min_time_between_api_calls,
                 delay_evaluation_by_seconds) -> None:
        """ Initialize the SolarAPI class """

    @abstractmethod
    def get_forecast(self) -> dict[int, float]:
        """ Get solar production of all installations up to next 48 hours """

    @abstractmethod
    def refresh_data(self, force: bool = False) -> None:
        """ Refresh data from provider (force: skip min. update interval) """