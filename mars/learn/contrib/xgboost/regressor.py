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


from ..utils import make_import_error_func
from .core import xgboost, XGBScikitLearnBase


XGBRegressor = make_import_error_func('xgboost')
if xgboost:
    from .core import wrap_evaluation_matrices
    from .train import train
    from .predict import predict

    class XGBRegressor(XGBScikitLearnBase):
        """
        Implementation of the scikit-learn API for XGBoost regressor.
        """

        def fit(self, X, y, sample_weight=None, base_margin=None,
                eval_set=None, sample_weight_eval_set=None, base_margin_eval_set=None, **kw):
            session = kw.pop('session', None)
            run_kwargs = kw.pop('run_kwargs', dict())
            if kw:
                raise TypeError(f"fit got an unexpected keyword argument '{next(iter(kw))}'")

            dtrain, evals = wrap_evaluation_matrices(
                None, X, y, sample_weight, base_margin, eval_set,
                sample_weight_eval_set, base_margin_eval_set)
            params = self.get_xgb_params()
            self.evals_result_ = dict()
            result = train(params, dtrain, num_boost_round=self.get_num_boosting_rounds(),
                           evals=evals, evals_result=self.evals_result_,
                           session=session, run_kwargs=run_kwargs)
            self._Booster = result
            return self

        def predict(self, data, **kw):
            session = kw.pop('session', None)
            run_kwargs = kw.pop('run_kwargs', None)
            return predict(self.get_booster(), data,
                           session=session, run_kwargs=run_kwargs, **kw)
