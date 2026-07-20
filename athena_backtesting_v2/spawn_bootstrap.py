"""Side-effect-free Windows multiprocessing bootstrap for backtest workers.

The worker target is imported from ``athena_backtesting_v2.runner`` after this
file is executed by Python's spawn machinery. Keeping this file empty of app
imports prevents workers from registering Flask routes or starting schedulers.
"""

