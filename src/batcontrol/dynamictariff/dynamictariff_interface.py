""" Interface for tariff classes """

from abc import ABC, abstractmethod

class TariffInterface(ABC):
    """ Interface for tariff classes """
    @abstractmethod
    def __init__(self, timezone, min_time_between_api_calls, delay_evaluation_by_seconds):
        """ Initialize the tariff class """

    @abstractmethod
    def get_prices(self) -> dict[int, float]:
        """ get prices in processable format with hours as keys """

    @abstractmethod
    def refresh_data(self, force: bool = False) -> None:
        """ Refresh data from provider.

        Args:
            force: When True, bypass cache and always fetch fresh data.
        """