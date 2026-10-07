""" Interface for consumption forecast classes """

from abc import ABC, abstractmethod


class ForecastConsumptionInterface(ABC):
    """ Interface for ConsumptionAPI classes """

    @abstractmethod
    def get_forecast(self, requested_slots) -> dict[int, float]:
        """ Get consumption forecast for the next `requested_slots` slots

        Args:
            requested_slots: Number of slots at the target resolution the
                provider was initialized with (15 or 60 minutes), starting
                with the current slot. Limited to 48 hours worth of data.
        """

    @abstractmethod
    def refresh_data(self) -> None:
        """ Refresh/update forecast data from source """
