import time
import weakref
from typing import Optional, Dict

from ..utils.callback import Callbacks


class ServiceError(Exception):
    pass


class Service(Callbacks):
    """Abstract base class for a service: a part of the SAXS instrument which
    takes care for a well-defined job, such as keeping track of file sequence
    numbers or running data reduction on finished exposures.

    Some services can also have tasks to be run at regular time intervals. The
    responsibility of installing and removing the needed timeout handlers rests
    on the methods start() and stop(), to be overridden in subclasses.



    """

    name = '__abstract__'

    starttime = None

    state = {}

    def __init__(self, instrument, configdir: str, statedict: Optional[Dict] = None):
        Callbacks.__init__(self)
        self.state = self.__class__.state.copy()
        if not isinstance(instrument, weakref.ProxyTypes):
            instrument = weakref.proxy(instrument)
        self.instrument = instrument
        self.configdir = configdir
        self.load_state(statedict)

    def load_state(self, dictionary):
        """Load the state from a dictionary"""
        self.state.update(dictionary)
        return

    def save_state(self):
        """Save the state to a dictionary"""
        return self.state.copy()

    def start(self):
        """Start operation."""
        self.starttime = time.monotonic()

    def update_config(self, dictionary):
        pass

    def stop(self):
        """Stop operation."""
        self.starttime = None
