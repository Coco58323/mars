# Copyright 1999-2021 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import os

import numpy as np
import pandas as pd
import pytest

from .... import tensor as mt, dataframe as md, execute
from ....core import enter_mode
from .._bagging import _extract_bagging_io, BaggingSample, \
    BaggingSampleReindex


def _get_tileable_chunk_data(sync_session, tileable):
    @enter_mode(build=True)
    async def _async_fetch():
        tuples = []
        async_session = sync_session._session
        meta_api = async_session._meta_api

        t, indexes = async_session._get_to_fetch_tileable(tileable)

        delays = [meta_api.get_chunk_meta.delay(chunk.key, fields=['bands'])
                  for chunk in t.chunks]
        band_infos = await meta_api.get_chunk_meta.batch(*delays)
        for chunk, band_info in zip(t.chunks, band_infos):
            band = band_info['bands'][0]
            storage_api = await async_session._get_storage_api(band)
            data = await storage_api.get(chunk.key)
            tuples.append((t, chunk, data))
        return tuples

    future = asyncio.run_coroutine_threadsafe(
        _async_fetch(), sync_session._isolation.loop)
    return future.result(120 if 'CI' in os.environ else None)


@pytest.mark.parametrize(
    'use_dataframe, max_samples, max_features, with_labels, with_weights',
    [
        (False, 10, 1.0, False, False),
        (False, 10, 0.5, True, True),
        (True, 10, 1.0, False, False),
        (True, 10, 0.5, True, True),
    ]
)
def test_bagging_sample_execution(
    setup, use_dataframe, max_samples, max_features,
    with_labels, with_weights
):
    rs = np.random.RandomState(0)

    raw_data = rs.randint(100, size=(100, 50))
    if not use_dataframe:
        t = mt.tensor(raw_data, chunk_size=20)
    else:
        raw_data = pd.DataFrame(raw_data)
        t = md.DataFrame(raw_data, chunk_size=20)

    raw_labels = rs.choice([0, 1, 2], size=100)
    raw_weights = rs.random(100)
    labels = mt.tensor(raw_labels, chunk_size=20) if with_labels else None
    weights = mt.tensor(raw_weights, chunk_size=20) if with_weights else None

    sample_op = BaggingSample(n_estimators=10, max_samples=max_samples,
                              max_features=max_features, random_state=rs)
    result_tuple = execute(*sample_op(t, labels, weights))
    t_sampled, t_labels, t_weights, t_feature_indices \
        = _extract_bagging_io(result_tuple, sample_op, output=True)

    label_chunks, weights_chunks, feature_idx_chunks = dict(), dict(), dict()

    for t, chunks_dict in zip((t_labels, t_weights), (label_chunks, weights_chunks)):
        if t is None:
            continue
        for tiled, chunk, chunk_data in _get_tileable_chunk_data(setup, t):
            assert len(tiled.chunks) == 5
            chunks_dict[chunk.index] = chunk_data
            for d in chunk_data:
                assert d.shape == (10,)

    if t_feature_indices is not None:
        for tiled, chunk, chunk_data in _get_tileable_chunk_data(setup, t_feature_indices):
            assert len(tiled.chunks) == 5
            feature_idx_chunks[chunk.index] = chunk_data
            assert chunk_data.shape == (2, int(max_features * raw_data.shape[1]))

    for tiled, chunk, chunk_data in _get_tileable_chunk_data(setup, t_sampled):
        assert len(tiled.chunks) == 5
        assert len(chunk_data) == 2
        for est_id, d in enumerate(chunk_data):
            assert d.shape == (10, int(max_features * raw_data.shape[1]))

            if use_dataframe:
                raw_sliced = raw_data.loc[d.index]
                if label_chunks:
                    label_chunk = label_chunks[(chunk.index[0],)][est_id]
                    np.testing.assert_array_equal(raw_labels[d.index], label_chunk)
                if weights_chunks:
                    weights_chunk = weights_chunks[(chunk.index[0],)][est_id]
                    np.testing.assert_array_equal(raw_weights[d.index], weights_chunk)

                if feature_idx_chunks:
                    feature_indices_chunk = feature_idx_chunks[chunk.index][est_id]
                    raw_sliced = raw_sliced.iloc[:, feature_indices_chunk]
                pd.testing.assert_frame_equal(raw_sliced, d)


@pytest.mark.parametrize(
    'use_dataframe, max_samples, max_features, column_split',
    [
        (False, 10, 1.0, 50),
        (False, 10, 0.5, 50),
        (True, 10, 1.0, 20),
        (True, 10, 0.5, 20),
    ]
)
def test_bagging_sample_reindex(
    setup, use_dataframe, max_samples, max_features, column_split
):
    rs = np.random.RandomState(0)

    raw_insts = rs.randint(100, size=(100, 50))
    raw_data = rs.randint(100, size=(200, 50))
    if not use_dataframe:
        t_insts = mt.tensor(raw_insts, chunk_size=column_split)
        t_data = mt.tensor(raw_data, chunk_size=column_split)
    else:
        raw_insts = pd.DataFrame(raw_insts)
        raw_data = pd.DataFrame(raw_data)
        t_insts = md.DataFrame(raw_insts, chunk_size=column_split)
        t_data = md.DataFrame(raw_data, chunk_size=column_split)

    sample_op = BaggingSample(n_estimators=10, max_samples=max_samples,
                              max_features=max_features, random_state=rs)
    result_tuple = execute(*sample_op(t_insts))
    _t_sampled, _label, _weights, t_feature_indices \
        = _extract_bagging_io(result_tuple, sample_op, output=True)

    reindex_op = BaggingSampleReindex(n_estimators=10)
    reindexed = execute(reindex_op(t_data, t_feature_indices),
                        extra_config={'check_dtypes': False})

    for tiled, _chunk, chunk_data in _get_tileable_chunk_data(setup, reindexed):
        if t_feature_indices is None:
            assert len(tiled.chunks) == np.ceil(raw_data.shape[0] / column_split)
            assert chunk_data.shape[1] == 50
        else:
            row_chunks = np.ceil(raw_insts.shape[0] / column_split)
            assert len(tiled.chunks) == row_chunks * np.ceil(raw_data.shape[0] / column_split)
            assert isinstance(chunk_data, tuple)
            for chunk_data_piece in chunk_data:
                assert chunk_data_piece.shape[1] == 25
