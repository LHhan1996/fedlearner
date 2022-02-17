import os
import tensorflow as tf
import numpy as np
from fedlearner.cluster.cluster_spec import FLClusterSpec
from fedlearner.common import metrics
from .master import LeaderMaster, FollowerMaster
from ._global_context import global_context as _gtx


class MasterControlKerasCallback(tf.keras.callbacks.Callback):

    def __init__(self, master):
        self._master = master
        super().__init__()

    def on_train_begin(self, logs):
        self._master.on_train_begin()

    def on_train_end(self, logs):
        self._master.on_train_end()

    def on_train_batch_begin(self, batch, logs=None):
        self._master.on_train_batch_begin()

    def on_train_batch_end(self, batch, logs=None):
        self._master.on_train_batch_end()
        self.emit_metrics(logs)

    def emit_metrics(self, logs):
        if 'batch' not in logs:
            return
        global_step = logs['batch']

        stats_pipe = _gtx.stats_client.pipeline()
        stats_pipe.gauge("trainer.metric_global_step", global_step)
        for key, value in logs.items():
            if key in ('size', 'batch'):
                continue
            stats_pipe.gauge("trainer.metric_value",
                             value, tags={"metric": key})
            metrics.emit_store(name=key, value=logs[key])
        stats_pipe.send()


def train_from_keras_model(model,
                           x=None,
                           y=None,
                           batch_size=None,
                           epochs=1,
                           fl_name=None,
                           fl_cluster=None,
                           steps_per_sync=None,
                           save_filepath=None):

    if not fl_name:
        fl_name = os.getenv("FL_NAME")
    if not fl_cluster:
        fl_cluster = os.getenv("FL_CLUSTER")
    if not steps_per_sync:
        steps_per_sync = int(os.getenv("FL_STPES_PER_SYNC"))
    if not save_filepath:
        save_filepath = os.getenv("FL_SAVE_FILEPATH") or os.getenv(
            "EXPORT_PATH")

    fl_cluster_spec = FLClusterSpec(fl_cluster)
    if fl_cluster_spec.is_leader(fl_name):
        master_class = LeaderMaster
    elif fl_cluster_spec.is_follower(fl_name):
        master_class = FollowerMaster
    else:
        raise ValueError("unknow fl_name: {}".format(fl_name))

    master = master_class(model, fl_name, fl_cluster_spec, steps_per_sync,
                          save_filepath)
    master.start()
    model.fit(x,
              y,
              batch_size=batch_size,
              epochs=epochs,
              callbacks=[MasterControlKerasCallback(master)])

    master.wait()
