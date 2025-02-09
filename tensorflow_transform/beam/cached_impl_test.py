# coding=utf-8
#
# Copyright 2018 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for cached tf.Transform analysis."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import collections
import itertools
import os
import struct
# GOOGLE-INITIALIZATION
import apache_beam as beam
from apache_beam.testing import util as beam_test_util
import numpy as np

import six
import tensorflow as tf
import tensorflow_transform as tft
from tensorflow_transform import analyzer_nodes
from tensorflow_transform import impl_helper
from tensorflow_transform import nodes
import tensorflow_transform.beam as tft_beam
from tensorflow_transform.beam import analysis_graph_builder
from tensorflow_transform.beam import analyzer_cache
from tensorflow_transform.beam import tft_unit
from tensorflow_transform.tf_metadata import dataset_metadata
from tensorflow_transform.tf_metadata import schema_utils

mock = tf.compat.v1.test.mock

_TEST_CACHE_VERSION = analyzer_cache._CACHE_VERSION


def _get_counter_value(metrics, name):
  metric = metrics.query(
      beam.metrics.metric.MetricsFilter().with_name(name))['counters']
  committed = sum([r.committed for r in metric])
  attempted = sum([r.attempted for r in metric])
  assert committed == attempted, '{} != {}'.format(committed, attempted)
  return committed


def _encode_vocabulary_accumulator(token_bytes, value_bytes):
  return struct.pack('qq{}s{}s'.format(len(token_bytes), len(value_bytes)),
                     len(token_bytes), len(value_bytes), token_bytes,
                     value_bytes)


class _TestPipeline(beam.Pipeline):

  @property
  def has_ran(self):
    return hasattr(self, '_run_result')

  @property
  def metrics(self):
    if not self.has_ran:
      raise RuntimeError('Pipeline has to run before accessing its metrics')
    return self._run_result.metrics()

  def __exit__(self, exc_type, exc_val, exc_tb):
    if not exc_type:
      assert not self.has_ran
      self._run_result = self.run()
      self._run_result.wait_until_finish()


def _preprocessing_fn_for_common_optimize_traversal(inputs):
  _ = tft.vocabulary(inputs['s'])
  x = inputs['x']
  x_mean = tft.mean(x, name='x')
  x_square_deviations = tf.square(x - x_mean)

  # 2nd analysis phase defined here.
  x_var = tft.mean(x_square_deviations, name='x_square_deviations')
  x_normalized = (x - x_mean) / tf.sqrt(x_var)
  return {'x_normalized': x_normalized}


_OPTIMIZE_TRAVERSAL_COMMON_CASE = dict(
    testcase_name='common',
    feature_spec={
        'x': tf.io.FixedLenFeature([], tf.float32),
        's': tf.io.FixedLenFeature([], tf.string)
    },
    preprocessing_fn=_preprocessing_fn_for_common_optimize_traversal,
    dataset_input_cache_dict={
        _TEST_CACHE_VERSION +
        b'CacheableCombineAccumulate[x/mean_and_var]-HASH':
            'cache hit',
    },
    expected_dot_graph_str=r"""digraph G {
directed=True;
node [shape=Mrecord];
"CreateSavedModelForAnalyzerInputs[Phase0]" [label="{CreateSavedModel|table_initializers: 0|output_signature: OrderedDict([('vocabulary/Reshape', \"Tensor\<shape: [None], \<dtype: 'string'\>\>\"), ('x/mean_and_var/Cast', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\"), ('x/mean_and_var/truediv', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\"), ('x/mean_and_var/truediv_1', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\"), ('x/mean_and_var/zeros', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\")])|label: CreateSavedModelForAnalyzerInputs[Phase0]}"];
"ApplySavedModel[Phase0][AnalysisIndex0]" [label="{ApplySavedModel|dataset_key: span-0|phase: 0|label: ApplySavedModel[Phase0][AnalysisIndex0]|partitionable: True}"];
"CreateSavedModelForAnalyzerInputs[Phase0]" -> "ApplySavedModel[Phase0][AnalysisIndex0]";
"TensorSource[vocabulary][AnalysisIndex0]" [label="{ExtractFromDict|keys: ('vocabulary/Reshape',)|label: TensorSource[vocabulary][AnalysisIndex0]|partitionable: True}"];
"ApplySavedModel[Phase0][AnalysisIndex0]" -> "TensorSource[vocabulary][AnalysisIndex0]";
"VocabularyAccumulate[vocabulary][AnalysisIndex0]" [label="{VocabularyAccumulate|vocab_ordering_type: 1|input_dtype: string|label: VocabularyAccumulate[vocabulary][AnalysisIndex0]|partitionable: True}"];
"TensorSource[vocabulary][AnalysisIndex0]" -> "VocabularyAccumulate[vocabulary][AnalysisIndex0]";
"ApplySavedModel[Phase0][AnalysisIndex1]" [label="{ApplySavedModel|dataset_key: span-1|phase: 0|label: ApplySavedModel[Phase0][AnalysisIndex1]|partitionable: True}"];
"CreateSavedModelForAnalyzerInputs[Phase0]" -> "ApplySavedModel[Phase0][AnalysisIndex1]";
"TensorSource[vocabulary][AnalysisIndex1]" [label="{ExtractFromDict|keys: ('vocabulary/Reshape',)|label: TensorSource[vocabulary][AnalysisIndex1]|partitionable: True}"];
"ApplySavedModel[Phase0][AnalysisIndex1]" -> "TensorSource[vocabulary][AnalysisIndex1]";
"VocabularyAccumulate[vocabulary][AnalysisIndex1]" [label="{VocabularyAccumulate|vocab_ordering_type: 1|input_dtype: string|label: VocabularyAccumulate[vocabulary][AnalysisIndex1]|partitionable: True}"];
"TensorSource[vocabulary][AnalysisIndex1]" -> "VocabularyAccumulate[vocabulary][AnalysisIndex1]";
"FlattenCache[VocabularyMerge[vocabulary]]" [label="{Flatten|label: FlattenCache[VocabularyMerge[vocabulary]]|partitionable: True}"];
"VocabularyAccumulate[vocabulary][AnalysisIndex0]" -> "FlattenCache[VocabularyMerge[vocabulary]]";
"VocabularyAccumulate[vocabulary][AnalysisIndex1]" -> "FlattenCache[VocabularyMerge[vocabulary]]";
"VocabularyMerge[vocabulary]" [label="{VocabularyMerge|vocab_ordering_type: 1|use_adjusted_mutual_info: False|min_diff_from_avg: None|label: VocabularyMerge[vocabulary]}"];
"FlattenCache[VocabularyMerge[vocabulary]]" -> "VocabularyMerge[vocabulary]";
"VocabularyPrune[vocabulary]" [label="{VocabularyPrune|top_k: None|frequency_threshold: None|coverage_top_k: None|coverage_frequency_threshold: None|key_fn: None|label: VocabularyPrune[vocabulary]}"];
"VocabularyMerge[vocabulary]" -> "VocabularyPrune[vocabulary]";
"VocabularyOrderAndWrite[vocabulary]" [label="{VocabularyOrderAndWrite|vocab_filename: vocab_vocabulary|store_frequency: False|input_dtype: string|label: VocabularyOrderAndWrite[vocabulary]|fingerprint_shuffle: False}"];
"VocabularyPrune[vocabulary]" -> "VocabularyOrderAndWrite[vocabulary]";
"CreateTensorBinding[vocabulary/Placeholder]" [label="{CreateTensorBinding|tensor: vocabulary/Placeholder:0|is_asset_filepath: True|label: CreateTensorBinding[vocabulary/Placeholder]}"];
"VocabularyOrderAndWrite[vocabulary]" -> "CreateTensorBinding[vocabulary/Placeholder]";
"DecodeCache[CacheableCombineAccumulate[x/mean_and_var]][AnalysisIndex0]" [label="{DecodeCache|dataset_key: span-0|cache_key: \<bytes\>|coder: \<JsonNumpyCacheCoder\>|label: DecodeCache[CacheableCombineAccumulate[x/mean_and_var]][AnalysisIndex0]|partitionable: True}"];
"TensorSource[x/mean_and_var][AnalysisIndex1]" [label="{ExtractFromDict|keys: ('x/mean_and_var/Cast', 'x/mean_and_var/truediv', 'x/mean_and_var/truediv_1', 'x/mean_and_var/zeros')|label: TensorSource[x/mean_and_var][AnalysisIndex1]|partitionable: True}"];
"ApplySavedModel[Phase0][AnalysisIndex1]" -> "TensorSource[x/mean_and_var][AnalysisIndex1]";
"CacheableCombineAccumulate[x/mean_and_var][AnalysisIndex1]" [label="{CacheableCombineAccumulate|combiner: \<WeightedMeanAndVarCombiner\>|label: CacheableCombineAccumulate[x/mean_and_var][AnalysisIndex1]|partitionable: True}"];
"TensorSource[x/mean_and_var][AnalysisIndex1]" -> "CacheableCombineAccumulate[x/mean_and_var][AnalysisIndex1]";
"FlattenCache[CacheableCombineMerge[x/mean_and_var]]" [label="{Flatten|label: FlattenCache[CacheableCombineMerge[x/mean_and_var]]|partitionable: True}"];
"DecodeCache[CacheableCombineAccumulate[x/mean_and_var]][AnalysisIndex0]" -> "FlattenCache[CacheableCombineMerge[x/mean_and_var]]";
"CacheableCombineAccumulate[x/mean_and_var][AnalysisIndex1]" -> "FlattenCache[CacheableCombineMerge[x/mean_and_var]]";
"CacheableCombineMerge[x/mean_and_var]" [label="{CacheableCombineMerge|combiner: \<WeightedMeanAndVarCombiner\>|label: CacheableCombineMerge[x/mean_and_var]|{<0>0|<1>1}}"];
"FlattenCache[CacheableCombineMerge[x/mean_and_var]]" -> "CacheableCombineMerge[x/mean_and_var]";
"CreateTensorBinding[x/mean_and_var/Placeholder]" [label="{CreateTensorBinding|tensor: x/mean_and_var/Placeholder:0|is_asset_filepath: False|label: CreateTensorBinding[x/mean_and_var/Placeholder]}"];
"CacheableCombineMerge[x/mean_and_var]":0 -> "CreateTensorBinding[x/mean_and_var/Placeholder]";
"CreateTensorBinding[x/mean_and_var/Placeholder_1]" [label="{CreateTensorBinding|tensor: x/mean_and_var/Placeholder_1:0|is_asset_filepath: False|label: CreateTensorBinding[x/mean_and_var/Placeholder_1]}"];
"CacheableCombineMerge[x/mean_and_var]":1 -> "CreateTensorBinding[x/mean_and_var/Placeholder_1]";
"CreateSavedModelForAnalyzerInputs[Phase1]" [label="{CreateSavedModel|table_initializers: 0|output_signature: OrderedDict([('x_square_deviations/mean_and_var/Cast', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\"), ('x_square_deviations/mean_and_var/truediv', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\"), ('x_square_deviations/mean_and_var/truediv_1', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\"), ('x_square_deviations/mean_and_var/zeros', \"Tensor\<shape: [], \<dtype: 'float32'\>\>\")])|label: CreateSavedModelForAnalyzerInputs[Phase1]}"];
"CreateTensorBinding[vocabulary/Placeholder]" -> "CreateSavedModelForAnalyzerInputs[Phase1]";
"CreateTensorBinding[x/mean_and_var/Placeholder]" -> "CreateSavedModelForAnalyzerInputs[Phase1]";
"CreateTensorBinding[x/mean_and_var/Placeholder_1]" -> "CreateSavedModelForAnalyzerInputs[Phase1]";
"ApplySavedModel[Phase1]" [label="{ApplySavedModel|dataset_key: None|phase: 1|label: ApplySavedModel[Phase1]|partitionable: True}"];
"CreateSavedModelForAnalyzerInputs[Phase1]" -> "ApplySavedModel[Phase1]";
"TensorSource[x_square_deviations/mean_and_var]" [label="{ExtractFromDict|keys: ('x_square_deviations/mean_and_var/Cast', 'x_square_deviations/mean_and_var/truediv', 'x_square_deviations/mean_and_var/truediv_1', 'x_square_deviations/mean_and_var/zeros')|label: TensorSource[x_square_deviations/mean_and_var]|partitionable: True}"];
"ApplySavedModel[Phase1]" -> "TensorSource[x_square_deviations/mean_and_var]";
"CacheableCombineAccumulate[x_square_deviations/mean_and_var]" [label="{CacheableCombineAccumulate|combiner: \<WeightedMeanAndVarCombiner\>|label: CacheableCombineAccumulate[x_square_deviations/mean_and_var]|partitionable: True}"];
"TensorSource[x_square_deviations/mean_and_var]" -> "CacheableCombineAccumulate[x_square_deviations/mean_and_var]";
"CacheableCombineMerge[x_square_deviations/mean_and_var]" [label="{CacheableCombineMerge|combiner: \<WeightedMeanAndVarCombiner\>|label: CacheableCombineMerge[x_square_deviations/mean_and_var]|{<0>0|<1>1}}"];
"CacheableCombineAccumulate[x_square_deviations/mean_and_var]" -> "CacheableCombineMerge[x_square_deviations/mean_and_var]";
"CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder]" [label="{CreateTensorBinding|tensor: x_square_deviations/mean_and_var/Placeholder:0|is_asset_filepath: False|label: CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder]}"];
"CacheableCombineMerge[x_square_deviations/mean_and_var]":0 -> "CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder]";
"CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder_1]" [label="{CreateTensorBinding|tensor: x_square_deviations/mean_and_var/Placeholder_1:0|is_asset_filepath: False|label: CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder_1]}"];
"CacheableCombineMerge[x_square_deviations/mean_and_var]":1 -> "CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder_1]";
CreateSavedModel [label="{CreateSavedModel|table_initializers: 0|output_signature: OrderedDict([('x_normalized', \"Tensor\<shape: [None], \<dtype: 'float32'\>\>\")])|label: CreateSavedModel}"];
"CreateTensorBinding[vocabulary/Placeholder]" -> CreateSavedModel;
"CreateTensorBinding[x/mean_and_var/Placeholder]" -> CreateSavedModel;
"CreateTensorBinding[x/mean_and_var/Placeholder_1]" -> CreateSavedModel;
"CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder]" -> CreateSavedModel;
"CreateTensorBinding[x_square_deviations/mean_and_var/Placeholder_1]" -> CreateSavedModel;
"EncodeCache[CacheableCombineAccumulate[x/mean_and_var]][AnalysisIndex1]" [label="{EncodeCache|coder: \<JsonNumpyCacheCoder\>|label: EncodeCache[CacheableCombineAccumulate[x/mean_and_var]][AnalysisIndex1]|partitionable: True}"];
"CacheableCombineAccumulate[x/mean_and_var][AnalysisIndex1]" -> "EncodeCache[CacheableCombineAccumulate[x/mean_and_var]][AnalysisIndex1]";
"EncodeCache[VocabularyAccumulate[vocabulary]][AnalysisIndex0]" [label="{EncodeCache|coder: \<_VocabularyAccumulatorCoder\>|label: EncodeCache[VocabularyAccumulate[vocabulary]][AnalysisIndex0]|partitionable: True}"];
"VocabularyAccumulate[vocabulary][AnalysisIndex0]" -> "EncodeCache[VocabularyAccumulate[vocabulary]][AnalysisIndex0]";
"EncodeCache[VocabularyAccumulate[vocabulary]][AnalysisIndex1]" [label="{EncodeCache|coder: \<_VocabularyAccumulatorCoder\>|label: EncodeCache[VocabularyAccumulate[vocabulary]][AnalysisIndex1]|partitionable: True}"];
"VocabularyAccumulate[vocabulary][AnalysisIndex1]" -> "EncodeCache[VocabularyAccumulate[vocabulary]][AnalysisIndex1]";
}
""")


def _preprocessing_fn_for_generalized_chained_ptransforms(inputs):

  class FakeChainablePartitionable(
      collections.namedtuple('FakeChainablePartitionable', ['label']),
      nodes.OperationDef):

    def __new__(cls, label=None):
      if label is None:
        scope = tf.compat.v1.get_default_graph().get_name_scope()
        label = '{}[{}]'.format(cls.__name__, scope)
      return super(FakeChainablePartitionable, cls).__new__(cls, label=label)

    @property
    def num_outputs(self):
      return 1

    @property
    def is_partitionable(self):
      return True

  class FakeChainableCacheable(
      collections.namedtuple('FakeChainableCacheable', ['label']),
      nodes.OperationDef):

    def __new__(cls, label=None):
      if label is None:
        scope = tf.compat.v1.get_default_graph().get_name_scope()
        label = '{}[{}]'.format(cls.__name__, scope)
      return super(FakeChainableCacheable, cls).__new__(cls, label=label)

    @property
    def num_outputs(self):
      return 1

    @property
    def is_partitionable(self):
      return True

    @property
    def cache_coder(self):
      return 'Not-a-coder-but-thats-ok!'

  class FakeChainable(
      collections.namedtuple('FakeChainable', ['label']), nodes.OperationDef):

    def __new__(cls, label=None):
      if label is None:
        scope = tf.compat.v1.get_default_graph().get_name_scope()
        label = '{}[{}]'.format(cls.__name__, scope)
      return super(FakeChainable, cls).__new__(cls, label=label)

    @property
    def num_outputs(self):
      return 1

    @property
    def is_partitionable(self):
      return False

  with tf.compat.v1.name_scope('x'):
    input_values_node = nodes.apply_operation(
        analyzer_nodes.TensorSource, tensors=[inputs['x']])
    with tf.compat.v1.name_scope('partitionable1'):
      partitionable_outputs = nodes.apply_multi_output_operation(
          FakeChainablePartitionable, input_values_node)
    with tf.compat.v1.name_scope('cacheable1'):
      intermediate_cached_value_node = nodes.apply_multi_output_operation(
          FakeChainableCacheable, *partitionable_outputs)
    with tf.compat.v1.name_scope('partitionable2'):
      partitionable_outputs = nodes.apply_multi_output_operation(
          FakeChainablePartitionable, *intermediate_cached_value_node)
    with tf.compat.v1.name_scope('cacheable2'):
      cached_value_node = nodes.apply_multi_output_operation(
          FakeChainableCacheable, *partitionable_outputs)
    with tf.compat.v1.name_scope('partitionable3'):
      output_value_node = nodes.apply_multi_output_operation(
          FakeChainablePartitionable, *cached_value_node)
    with tf.compat.v1.name_scope('merge'):
      output_value_node = nodes.apply_operation(FakeChainable,
                                                *output_value_node)
    with tf.compat.v1.name_scope('not-cacheable'):
      non_cached_output = nodes.apply_operation(FakeChainable,
                                                input_values_node)
    x_chained = analyzer_nodes.bind_future_as_tensor(
        output_value_node, analyzer_nodes.TensorInfo(tf.float32, (17, 27),
                                                     False))
    x_plain = analyzer_nodes.bind_future_as_tensor(
        non_cached_output, analyzer_nodes.TensorInfo(tf.int64, (7, 13), False))
    return {'x_chained': x_chained, 'x_plain': x_plain}


_OPTIMIZE_TRAVERSAL_GENERALIZED_CHAINED_PTRANSFORMS_CASE = dict(
    testcase_name='generalized_chained_ptransforms',
    feature_spec={'x': tf.io.FixedLenFeature([], tf.float32)},
    preprocessing_fn=_preprocessing_fn_for_generalized_chained_ptransforms,
    dataset_input_cache_dict=None,
    expected_dot_graph_str=r"""digraph G {
directed=True;
node [shape=Mrecord];
"CreateSavedModelForAnalyzerInputs[Phase0]" [label="{CreateSavedModel|table_initializers: 0|output_signature: OrderedDict([('inputs/x', \"Tensor\<shape: [None], \<dtype: 'float32'\>\>\")])|label: CreateSavedModelForAnalyzerInputs[Phase0]}"];
"ApplySavedModel[Phase0][AnalysisIndex0]" [label="{ApplySavedModel|dataset_key: span-0|phase: 0|label: ApplySavedModel[Phase0][AnalysisIndex0]|partitionable: True}"];
"CreateSavedModelForAnalyzerInputs[Phase0]" -> "ApplySavedModel[Phase0][AnalysisIndex0]";
"TensorSource[x][AnalysisIndex0]" [label="{ExtractFromDict|keys: ('inputs/x',)|label: TensorSource[x][AnalysisIndex0]|partitionable: True}"];
"ApplySavedModel[Phase0][AnalysisIndex0]" -> "TensorSource[x][AnalysisIndex0]";
"FakeChainablePartitionable[x/partitionable1][AnalysisIndex0]" [label="{FakeChainablePartitionable|label: FakeChainablePartitionable[x/partitionable1][AnalysisIndex0]|partitionable: True}"];
"TensorSource[x][AnalysisIndex0]" -> "FakeChainablePartitionable[x/partitionable1][AnalysisIndex0]";
"FakeChainableCacheable[x/cacheable1][AnalysisIndex0]" [label="{FakeChainableCacheable|label: FakeChainableCacheable[x/cacheable1][AnalysisIndex0]|partitionable: True}"];
"FakeChainablePartitionable[x/partitionable1][AnalysisIndex0]" -> "FakeChainableCacheable[x/cacheable1][AnalysisIndex0]";
"FakeChainablePartitionable[x/partitionable2][AnalysisIndex0]" [label="{FakeChainablePartitionable|label: FakeChainablePartitionable[x/partitionable2][AnalysisIndex0]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable1][AnalysisIndex0]" -> "FakeChainablePartitionable[x/partitionable2][AnalysisIndex0]";
"FakeChainableCacheable[x/cacheable2][AnalysisIndex0]" [label="{FakeChainableCacheable|label: FakeChainableCacheable[x/cacheable2][AnalysisIndex0]|partitionable: True}"];
"FakeChainablePartitionable[x/partitionable2][AnalysisIndex0]" -> "FakeChainableCacheable[x/cacheable2][AnalysisIndex0]";
"FakeChainablePartitionable[x/partitionable3][AnalysisIndex0]" [label="{FakeChainablePartitionable|label: FakeChainablePartitionable[x/partitionable3][AnalysisIndex0]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable2][AnalysisIndex0]" -> "FakeChainablePartitionable[x/partitionable3][AnalysisIndex0]";
"ApplySavedModel[Phase0][AnalysisIndex1]" [label="{ApplySavedModel|dataset_key: span-1|phase: 0|label: ApplySavedModel[Phase0][AnalysisIndex1]|partitionable: True}"];
"CreateSavedModelForAnalyzerInputs[Phase0]" -> "ApplySavedModel[Phase0][AnalysisIndex1]";
"TensorSource[x][AnalysisIndex1]" [label="{ExtractFromDict|keys: ('inputs/x',)|label: TensorSource[x][AnalysisIndex1]|partitionable: True}"];
"ApplySavedModel[Phase0][AnalysisIndex1]" -> "TensorSource[x][AnalysisIndex1]";
"FakeChainablePartitionable[x/partitionable1][AnalysisIndex1]" [label="{FakeChainablePartitionable|label: FakeChainablePartitionable[x/partitionable1][AnalysisIndex1]|partitionable: True}"];
"TensorSource[x][AnalysisIndex1]" -> "FakeChainablePartitionable[x/partitionable1][AnalysisIndex1]";
"FakeChainableCacheable[x/cacheable1][AnalysisIndex1]" [label="{FakeChainableCacheable|label: FakeChainableCacheable[x/cacheable1][AnalysisIndex1]|partitionable: True}"];
"FakeChainablePartitionable[x/partitionable1][AnalysisIndex1]" -> "FakeChainableCacheable[x/cacheable1][AnalysisIndex1]";
"FakeChainablePartitionable[x/partitionable2][AnalysisIndex1]" [label="{FakeChainablePartitionable|label: FakeChainablePartitionable[x/partitionable2][AnalysisIndex1]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable1][AnalysisIndex1]" -> "FakeChainablePartitionable[x/partitionable2][AnalysisIndex1]";
"FakeChainableCacheable[x/cacheable2][AnalysisIndex1]" [label="{FakeChainableCacheable|label: FakeChainableCacheable[x/cacheable2][AnalysisIndex1]|partitionable: True}"];
"FakeChainablePartitionable[x/partitionable2][AnalysisIndex1]" -> "FakeChainableCacheable[x/cacheable2][AnalysisIndex1]";
"FakeChainablePartitionable[x/partitionable3][AnalysisIndex1]" [label="{FakeChainablePartitionable|label: FakeChainablePartitionable[x/partitionable3][AnalysisIndex1]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable2][AnalysisIndex1]" -> "FakeChainablePartitionable[x/partitionable3][AnalysisIndex1]";
"FlattenCache[FakeChainable[x/merge]]" [label="{Flatten|label: FlattenCache[FakeChainable[x/merge]]|partitionable: True}"];
"FakeChainablePartitionable[x/partitionable3][AnalysisIndex0]" -> "FlattenCache[FakeChainable[x/merge]]";
"FakeChainablePartitionable[x/partitionable3][AnalysisIndex1]" -> "FlattenCache[FakeChainable[x/merge]]";
"FakeChainable[x/merge]" [label="{FakeChainable|label: FakeChainable[x/merge]}"];
"FlattenCache[FakeChainable[x/merge]]" -> "FakeChainable[x/merge]";
"CreateTensorBinding[x/Placeholder]" [label="{CreateTensorBinding|tensor: x/Placeholder:0|is_asset_filepath: False|label: CreateTensorBinding[x/Placeholder]}"];
"FakeChainable[x/merge]" -> "CreateTensorBinding[x/Placeholder]";
"ApplySavedModel[Phase0]" [label="{ApplySavedModel|dataset_key: None|phase: 0|label: ApplySavedModel[Phase0]|partitionable: True}"];
"CreateSavedModelForAnalyzerInputs[Phase0]" -> "ApplySavedModel[Phase0]";
"TensorSource[x]" [label="{ExtractFromDict|keys: ('inputs/x',)|label: TensorSource[x]|partitionable: True}"];
"ApplySavedModel[Phase0]" -> "TensorSource[x]";
"FakeChainable[x/not-cacheable]" [label="{FakeChainable|label: FakeChainable[x/not-cacheable]}"];
"TensorSource[x]" -> "FakeChainable[x/not-cacheable]";
"CreateTensorBinding[x/Placeholder_1]" [label="{CreateTensorBinding|tensor: x/Placeholder_1:0|is_asset_filepath: False|label: CreateTensorBinding[x/Placeholder_1]}"];
"FakeChainable[x/not-cacheable]" -> "CreateTensorBinding[x/Placeholder_1]";
CreateSavedModel [label="{CreateSavedModel|table_initializers: 0|output_signature: OrderedDict([('x_chained', \"Tensor\<shape: [17, 27], \<dtype: 'float32'\>\>\"), ('x_plain', \"Tensor\<shape: [7, 13], \<dtype: 'int64'\>\>\")])|label: CreateSavedModel}"];
"CreateTensorBinding[x/Placeholder]" -> CreateSavedModel;
"CreateTensorBinding[x/Placeholder_1]" -> CreateSavedModel;
"EncodeCache[FakeChainableCacheable[x/cacheable1]][AnalysisIndex0]" [label="{EncodeCache|coder: Not-a-coder-but-thats-ok!|label: EncodeCache[FakeChainableCacheable[x/cacheable1]][AnalysisIndex0]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable1][AnalysisIndex0]" -> "EncodeCache[FakeChainableCacheable[x/cacheable1]][AnalysisIndex0]";
"EncodeCache[FakeChainableCacheable[x/cacheable1]][AnalysisIndex1]" [label="{EncodeCache|coder: Not-a-coder-but-thats-ok!|label: EncodeCache[FakeChainableCacheable[x/cacheable1]][AnalysisIndex1]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable1][AnalysisIndex1]" -> "EncodeCache[FakeChainableCacheable[x/cacheable1]][AnalysisIndex1]";
"EncodeCache[FakeChainableCacheable[x/cacheable2]][AnalysisIndex0]" [label="{EncodeCache|coder: Not-a-coder-but-thats-ok!|label: EncodeCache[FakeChainableCacheable[x/cacheable2]][AnalysisIndex0]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable2][AnalysisIndex0]" -> "EncodeCache[FakeChainableCacheable[x/cacheable2]][AnalysisIndex0]";
"EncodeCache[FakeChainableCacheable[x/cacheable2]][AnalysisIndex1]" [label="{EncodeCache|coder: Not-a-coder-but-thats-ok!|label: EncodeCache[FakeChainableCacheable[x/cacheable2]][AnalysisIndex1]|partitionable: True}"];
"FakeChainableCacheable[x/cacheable2][AnalysisIndex1]" -> "EncodeCache[FakeChainableCacheable[x/cacheable2]][AnalysisIndex1]";
}
""")

_OPTIMIZE_TRAVERSAL_TEST_CASES = [
    _OPTIMIZE_TRAVERSAL_COMMON_CASE,
    _OPTIMIZE_TRAVERSAL_GENERALIZED_CHAINED_PTRANSFORMS_CASE,
]


def mock_out_cache_hash(test_fn):

  def _make_next_hashed_path_for_test(*unused_args):
    return b'HASH'

  def _run_test(*args, **kwargs):
    with mock.patch.object(analysis_graph_builder._OptimizeVisitor,
                           '_make_next_hashed_path',
                           _make_next_hashed_path_for_test):
      return test_fn(*args, **kwargs)

  return _run_test


class CachedImplTest(tft_unit.TransformTestCase):

  def setUp(self):
    super(CachedImplTest, self).setUp()
    self.base_test_dir = os.path.join(
        os.environ.get('TEST_UNDECLARED_OUTPUTS_DIR', self.get_temp_dir()),
        self._testMethodName)
    self._cache_dir = os.path.join(self.base_test_dir, 'cache')

    self._context = tft_beam.Context(temp_dir=self.get_temp_dir())
    self._context.__enter__()

  def tearDown(self):
    self._context.__exit__()
    super(CachedImplTest, self).tearDown()

  @mock_out_cache_hash
  def test_single_phase_mixed_analyzer_run_once(self):
    span_0_key = 'span-0'
    span_1_key = 'span-1'

    def preprocessing_fn(inputs):

      integerized_s = tft.compute_and_apply_vocabulary(inputs['s'])

      _ = tft.bucketize(inputs['x'], 2, name='bucketize')

      return {
          'integerized_s':
              integerized_s,
          'x_min':
              tft.min(inputs['x'], name='x') + tf.zeros_like(inputs['x']),
          'x_mean':
              tft.mean(inputs['x'], name='x') + tf.zeros_like(inputs['x']),
          'y_min':
              tft.min(inputs['y'], name='y') + tf.zeros_like(inputs['y']),
          'y_mean':
              tft.mean(inputs['y'], name='y') + tf.zeros_like(inputs['y']),
      }

    # Run AnalyzeAndTransform on some input data and compare with expected
    # output.
    input_data = [{'x': 12, 'y': 1, 's': 'd'}, {'x': 10, 'y': 1, 's': 'c'}]
    input_metadata = dataset_metadata.DatasetMetadata(
        schema_utils.schema_from_feature_spec({
            'x': tf.io.FixedLenFeature([], tf.float32),
            'y': tf.io.FixedLenFeature([], tf.float32),
            's': tf.io.FixedLenFeature([], tf.string),
        }))
    input_data_dict = {
        span_0_key: [{
            'x': -2,
            'y': 1,
            's': 'b',
        }, {
            'x': 4,
            'y': -4,
            's': 'b',
        }],
        span_1_key: input_data,
    }

    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(
          list(itertools.chain(*input_data_dict.values())))
      cache_dict = {
          span_0_key: {
              _TEST_CACHE_VERSION +
              b'CacheableCombineAccumulate[x_1/mean_and_var]-HASH':
                  p | 'CreateA' >> beam.Create([b'[2.0, 1.0, 9.0, 0.0]']),
              _TEST_CACHE_VERSION + b'CacheableCombineAccumulate[x/x]-HASH':
                  p | 'CreateB' >> beam.Create([b'[2.0, 4.0]']),
              _TEST_CACHE_VERSION +
              b'CacheableCombineAccumulate[y_1/mean_and_var]-HASH':
                  p | 'CreateC' >> beam.Create([b'[2.0, -1.5, 6.25, 0.0]']),
              _TEST_CACHE_VERSION + b'CacheableCombineAccumulate[y/y]-HASH':
                  p | 'CreateD' >> beam.Create([b'[4.0, 1.0]']),
          },
          span_1_key: {},
      }

      transform_fn, cache_output = (
          (flat_data, input_data_dict, cache_dict, input_metadata)
          | 'Analyze' >> tft_beam.AnalyzeDatasetWithCache(preprocessing_fn))
      _ = (cache_output | 'WriteCache' >> analyzer_cache.WriteAnalysisCacheToFS(
          p, self._cache_dir))

      transformed_dataset = ((
          (input_data_dict[span_1_key], input_metadata), transform_fn)
                             | 'Transform' >> tft_beam.TransformDataset())

      dot_string = nodes.get_dot_graph([analysis_graph_builder._ANALYSIS_GRAPH
                                       ]).to_string()
      self.WriteRenderedDotFile(dot_string)

      # The output cache should not have entries for the cache that is present
      # in the input cache.
      self.assertEqual(
          len(cache_output[span_0_key]),
          len(cache_output[span_1_key]) - 4)

      transformed_data, unused_transformed_metadata = transformed_dataset

      expected_transformed = [
          {
              'x_mean': 6.0,
              'x_min': -2.0,
              'y_mean': -0.25,
              'y_min': -4.0,
              'integerized_s': 1,
          },
          {
              'x_mean': 6.0,
              'x_min': -2.0,
              'y_mean': -0.25,
              'y_min': -4.0,
              'integerized_s': 2,
          },
      ]
      beam_test_util.assert_that(transformed_data,
                                 beam_test_util.equal_to(expected_transformed))

      transform_fn_dir = os.path.join(self.base_test_dir, 'transform_fn')
      _ = transform_fn | tft_beam.WriteTransformFn(transform_fn_dir)

    # 4 from analyzing 2 spans, and 2 from transform.
    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 6)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 4)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 8)
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 2)
    self.assertEqual(_get_counter_value(p.metrics, 'num_packed_combiners'), 1)

  def test_single_phase_run_twice(self):

    span_0_key = 'span-0'
    span_1_key = 'span-1'

    def preprocessing_fn(inputs):

      _ = tft.vocabulary(inputs['s'], vocab_filename='vocab1')

      _ = tft.bucketize(inputs['x'], 2, name='bucketize')

      y_cov = tft.covariance(tf.expand_dims(inputs['y'], axis=1), tf.float32)
      return {
          'x_min':
              tft.min(inputs['x'], name='x') + tf.zeros_like(inputs['x']),
          'x_mean':
              tft.mean(inputs['x'], name='x') + tf.zeros_like(inputs['x']),
          'y_min':
              tft.min(inputs['y'], name='y') + tf.zeros_like(inputs['y']),
          'y_mean':
              tft.mean(inputs['y'], name='y') + tf.zeros_like(inputs['y']),
          'y_cov':
              tf.math.reduce_sum(y_cov) + tf.zeros_like(inputs['y']),
          's_integerized':
              tft.compute_and_apply_vocabulary(
                  inputs['s'],
                  labels=inputs['label'],
                  use_adjusted_mutual_info=True),
      }

    input_metadata = dataset_metadata.DatasetMetadata(
        schema_utils.schema_from_feature_spec({
            'x': tf.io.FixedLenFeature([], tf.float32),
            'y': tf.io.FixedLenFeature([], tf.float32),
            's': tf.io.FixedLenFeature([], tf.string),
            'label': tf.io.FixedLenFeature([], tf.int64),
        }))
    input_data_dict = {
        span_0_key: [{
            'x': -2,
            'y': 1,
            's': 'a',
            'label': 0,
        }, {
            'x': 4,
            'y': -4,
            's': 'a',
            'label': 1,
        }, {
            'x': 5,
            'y': 11,
            's': 'a',
            'label': 1,
        }, {
            'x': 1,
            'y': -4,
            's': u'ȟᎥ𝒋ǩľḿꞑȯ𝘱𝑞𝗋𝘴'.encode('utf-8'),
            'label': 1,
        }],
        span_1_key: [{
            'x': 12,
            'y': 1,
            's': u'ȟᎥ𝒋ǩľḿꞑȯ𝘱𝑞𝗋𝘴'.encode('utf-8'),
            'label': 0
        }, {
            'x': 10,
            'y': 1,
            's': 'c',
            'label': 1
        }],
    }
    expected_vocabulary_contents = np.array(
        [b'a', u'ȟᎥ𝒋ǩľḿꞑȯ𝘱𝑞𝗋𝘴'.encode('utf-8'), b'c'],
        dtype=object)
    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(
          list(itertools.chain(*input_data_dict.values())))

      # wrap each value in input_data_dict as a pcoll.
      input_data_pcoll_dict = {}
      for a, b in six.iteritems(input_data_dict):
        input_data_pcoll_dict[a] = p | a >> beam.Create(b)

      transform_fn_1, cache_output = (
          (flat_data, input_data_pcoll_dict, {}, input_metadata)
          | 'Analyze' >> tft_beam.AnalyzeDatasetWithCache(preprocessing_fn))
      _ = (
          cache_output
          | 'WriteCache' >> analyzer_cache.WriteAnalysisCacheToFS(
              p, self._cache_dir))

      transformed_dataset = ((
          (input_data_pcoll_dict[span_1_key], input_metadata), transform_fn_1)
                             | 'Transform' >> tft_beam.TransformDataset())

      del input_data_pcoll_dict
      transformed_data, unused_transformed_metadata = transformed_dataset

      expected_transformed_data = [
          {
              'x_mean': 5.0,
              'x_min': -2.0,
              'y_mean': 1.0,
              'y_min': -4.0,
              'y_cov': 25.0,
              's_integerized': 0,
          },
          {
              'x_mean': 5.0,
              'x_min': -2.0,
              'y_mean': 1.0,
              'y_min': -4.0,
              'y_cov': 25.0,
              's_integerized': 2,
          },
      ]
      beam_test_util.assert_that(
          transformed_data,
          beam_test_util.equal_to(expected_transformed_data),
          label='first')

      transform_fn_dir = os.path.join(self.base_test_dir, 'transform_fn_1')
      _ = transform_fn_1 | tft_beam.WriteTransformFn(transform_fn_dir)

      for key in input_data_dict:
        self.assertIn(key, cache_output)
        self.assertEqual(8, len(cache_output[key]))

    tf_transform_output = tft.TFTransformOutput(transform_fn_dir)
    vocab1_path = tf_transform_output.vocabulary_file_by_name('vocab1')
    self.AssertVocabularyContents(vocab1_path, expected_vocabulary_contents)

    # 4 from analyzing 2 spans, and 2 from transform.
    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 8)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 0)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 16)
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 2)

    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(
          list(itertools.chain(*input_data_dict.values())))

      # wrap each value in input_data_dict as a pcoll.
      input_data_pcoll_dict = {}
      for a, b in six.iteritems(input_data_dict):
        input_data_pcoll_dict[a] = p | a >> beam.Create(b)

      input_cache = p | analyzer_cache.ReadAnalysisCacheFromFS(
          self._cache_dir, list(input_data_dict.keys()))

      transform_fn_2, second_output_cache = (
          (flat_data, input_data_pcoll_dict, input_cache, input_metadata)
          | 'AnalyzeAgain' >>
          (tft_beam.AnalyzeDatasetWithCache(preprocessing_fn)))
      _ = (
          second_output_cache
          | 'WriteCache' >> analyzer_cache.WriteAnalysisCacheToFS(
              p, self._cache_dir))

      dot_string = nodes.get_dot_graph([analysis_graph_builder._ANALYSIS_GRAPH
                                       ]).to_string()
      self.WriteRenderedDotFile(dot_string)

      transformed_dataset = ((
          (input_data_dict[span_1_key], input_metadata), transform_fn_2)
                             | 'TransformAgain' >> tft_beam.TransformDataset())
      transformed_data, unused_transformed_metadata = transformed_dataset
      beam_test_util.assert_that(
          transformed_data,
          beam_test_util.equal_to(expected_transformed_data),
          label='second')

      transform_fn_dir = os.path.join(self.base_test_dir, 'transform_fn_2')
      _ = transform_fn_2 | tft_beam.WriteTransformFn(transform_fn_dir)

    tf_transform_output = tft.TFTransformOutput(transform_fn_dir)
    vocab1_path = tf_transform_output.vocabulary_file_by_name('vocab1')
    self.AssertVocabularyContents(vocab1_path, expected_vocabulary_contents)

    self.assertFalse(second_output_cache)

    # Only 2 from transform.
    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 2)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 16)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 0)

    # The root CreateSavedModel is optimized away because the data doesn't get
    # processed at all (only cache).
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 1)

  @mock_out_cache_hash
  def test_caching_vocab_for_integer_categorical(self):

    span_0_key = 'span-0'
    span_1_key = 'span-1'

    def preprocessing_fn(inputs):
      return {
          'x_vocab':
              tft.compute_and_apply_vocabulary(
                  inputs['x'], frequency_threshold=2)
      }

    input_metadata = dataset_metadata.DatasetMetadata(
        schema_utils.schema_from_feature_spec({
            'x': tf.io.FixedLenFeature([], tf.int64),
        }))
    input_data_dict = {
        span_0_key: [{
            'x': -2,
        }, {
            'x': -4,
        }, {
            'x': -1,
        }, {
            'x': 4,
        }],
        span_1_key: [{
            'x': -2,
        }, {
            'x': -1,
        }, {
            'x': 6,
        }, {
            'x': 7,
        }],
    }
    expected_transformed_data = [{
        'x_vocab': 0,
    }, {
        'x_vocab': 1,
    }, {
        'x_vocab': -1,
    }, {
        'x_vocab': -1,
    }]
    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(
          list(itertools.chain(*input_data_dict.values())))

      cache_dict = {
          span_0_key: {
              _TEST_CACHE_VERSION +
              b'VocabularyAccumulate[compute_and_apply_vocabulary/vocabulary]'
              b'-HASH':
                  p | 'CreateB' >> beam.Create([
                      _encode_vocabulary_accumulator(b'-2', b'2'),
                      _encode_vocabulary_accumulator(b'-4', b'1'),
                      _encode_vocabulary_accumulator(b'-1', b'1'),
                      _encode_vocabulary_accumulator(b'4', b'1'),
                  ])
          },
          span_1_key: {},
      }

      transform_fn, cache_output = (
          (flat_data, input_data_dict, cache_dict, input_metadata)
          | 'Analyze' >> tft_beam.AnalyzeDatasetWithCache(preprocessing_fn))

      dot_string = nodes.get_dot_graph(
          [analysis_graph_builder._ANALYSIS_GRAPH]).to_string()
      self.WriteRenderedDotFile(dot_string)

      self.assertNotIn(span_0_key, cache_output)

      _ = cache_output | 'WriteCache' >> analyzer_cache.WriteAnalysisCacheToFS(
          p, self._cache_dir)

      transformed_dataset = ((
          (input_data_dict[span_1_key], input_metadata), transform_fn)
                             | 'Transform' >> tft_beam.TransformDataset())

      transformed_data, _ = transformed_dataset

      beam_test_util.assert_that(
          transformed_data,
          beam_test_util.equal_to(expected_transformed_data),
          label='first')

    # 4 from analysis since 1 span was completely cached, and 4 from transform.
    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 8)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 1)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 1)
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 2)

  @mock_out_cache_hash
  def test_non_frequency_vocabulary_merge(self):
    """This test compares vocabularies produced with and without cache."""

    mi_vocab_name = 'mutual_information_vocab'
    adjusted_mi_vocab_name = 'adjusted_mutual_information_vocab'
    weighted_frequency_vocab_name = 'weighted_frequency_vocab'

    def preprocessing_fn(inputs):
      _ = tft.vocabulary(
          inputs['s'],
          labels=inputs['label'],
          store_frequency=True,
          vocab_filename=mi_vocab_name,
          min_diff_from_avg=0.1,
          use_adjusted_mutual_info=False,
          name='with_mi')

      _ = tft.vocabulary(
          inputs['s'],
          labels=inputs['label'],
          store_frequency=True,
          vocab_filename=adjusted_mi_vocab_name,
          min_diff_from_avg=1.0,
          use_adjusted_mutual_info=True,
          name='with_adjusted_mi')

      _ = tft.vocabulary(
          inputs['s'],
          weights=inputs['weight'],
          store_frequency=True,
          vocab_filename=weighted_frequency_vocab_name,
          use_adjusted_mutual_info=False,
          name='with_weight')
      return inputs

    span_0_key = 'span-0'
    span_1_key = 'span-1'

    input_data = [
        dict(s='a', weight=1, label=1),
        dict(s='a', weight=0.5, label=1),
        dict(s='b', weight=0.75, label=1),
        dict(s='b', weight=1, label=0),
    ]
    input_metadata = dataset_metadata.DatasetMetadata(
        schema_utils.schema_from_feature_spec({
            's': tf.io.FixedLenFeature([], tf.string),
            'label': tf.io.FixedLenFeature([], tf.int64),
            'weight': tf.io.FixedLenFeature([], tf.float32),
        }))
    input_data_dict = {
        span_0_key: input_data,
        span_1_key: input_data,
    }

    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(
          list(itertools.chain(*input_data_dict.values())))

      # wrap each value in input_data_dict as a pcoll.
      input_data_pcoll_dict = {}
      for a, b in six.iteritems(input_data_dict):
        input_data_pcoll_dict[a] = p | a >> beam.Create(b)

      transform_fn_with_cache, output_cache = (
          (flat_data, input_data_pcoll_dict, {}, input_metadata)
          | tft_beam.AnalyzeDatasetWithCache(preprocessing_fn))
      transform_fn_with_cache_dir = os.path.join(self.base_test_dir,
                                                 'transform_fn_with_cache')
      _ = transform_fn_with_cache | tft_beam.WriteTransformFn(
          transform_fn_with_cache_dir)

      expected_accumulators = {
          _TEST_CACHE_VERSION + b'VocabularyAccumulate[with_mi]-HASH': [
              _encode_vocabulary_accumulator(
                  b'a', b'[2, [0.0, 1.0], [0.0, 0.0], 1.0]'),
              _encode_vocabulary_accumulator(
                  b'b', b'[2, [0.5, 0.5], [0.0, 0.0], 1.0]'),
              _encode_vocabulary_accumulator(
                  b'global_y_count_sentinel',
                  b'[4, [0.25, 0.75], [0.0, 0.0], 1.0]'),
          ],
          _TEST_CACHE_VERSION +
          b'VocabularyAccumulate[with_adjusted_mi]-HASH': [
              _encode_vocabulary_accumulator(
                  b'a', b'[2, [0.0, 1.0], [0.0, 0.0], 1.0]'),
              _encode_vocabulary_accumulator(
                  b'b', b'[2, [0.5, 0.5], [0.0, 0.0], 1.0]'),
              _encode_vocabulary_accumulator(
                  b'global_y_count_sentinel',
                  b'[4, [0.25, 0.75], [0.0, 0.0], 1.0]'),
          ],
          _TEST_CACHE_VERSION + b'VocabularyAccumulate[with_weight]-HASH': [
              _encode_vocabulary_accumulator(b'a', b'1.5'),
              _encode_vocabulary_accumulator(b'b', b'1.75')
          ],
      }

      spans = [span_0_key, span_1_key]
      self.assertCountEqual(output_cache.keys(), spans)
      for span in spans:
        self.assertCountEqual(output_cache[span].keys(),
                              expected_accumulators.keys())
        for idx, (key,
                  value) in enumerate(six.iteritems(expected_accumulators)):
          beam_test_util.assert_that(
              output_cache[span][key],
              beam_test_util.equal_to(value),
              label='AssertCache[{}][{}]'.format(span, idx))

    # 4 from analysis on each of the input spans.
    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 8)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 0)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 6)
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 2)

    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(input_data * 2)

      transform_fn_no_cache = ((flat_data, input_metadata)
                               | tft_beam.AnalyzeDataset(preprocessing_fn))

      transform_fn_no_cache_dir = os.path.join(self.base_test_dir,
                                               'transform_fn_no_cache')
      _ = transform_fn_no_cache | tft_beam.WriteTransformFn(
          transform_fn_no_cache_dir)

    # 4 from analysis on each of the input spans.
    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 8)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 0)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 0)
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 2)

    tft_output_cache = tft.TFTransformOutput(transform_fn_with_cache_dir)
    tft_output_no_cache = tft.TFTransformOutput(transform_fn_no_cache_dir)

    for vocab_filename in (mi_vocab_name, adjusted_mi_vocab_name,
                           weighted_frequency_vocab_name):
      cache_path = tft_output_cache.vocabulary_file_by_name(vocab_filename)
      no_cache_path = tft_output_no_cache.vocabulary_file_by_name(
          vocab_filename)
      with tf.io.gfile.GFile(cache_path, 'rb') as f1, tf.io.gfile.GFile(
          no_cache_path, 'rb') as f2:
        self.assertEqual(
            f1.readlines(), f2.readlines(),
            'vocab with cache != vocab without cache for: {}'.format(
                vocab_filename))

  @tft_unit.named_parameters(*_OPTIMIZE_TRAVERSAL_TEST_CASES)
  @mock_out_cache_hash
  def test_optimize_traversal(self, feature_spec, preprocessing_fn,
                              dataset_input_cache_dict, expected_dot_graph_str):
    span_0_key, span_1_key = 'span-0', 'span-1'
    if dataset_input_cache_dict is not None:
      cache = {span_0_key: dataset_input_cache_dict}
    else:
      cache = {}

    with tf.compat.v1.Graph().as_default() as graph:
      with tf.compat.v1.name_scope('inputs'):
        input_signature = impl_helper.feature_spec_as_batched_placeholders(
            feature_spec)
      output_signature = preprocessing_fn(input_signature)
      transform_fn_future, cache_output_dict = analysis_graph_builder.build(
          graph, input_signature, output_signature, {span_0_key, span_1_key},
          cache)

    leaf_nodes = [transform_fn_future] + sorted(
        cache_output_dict.values(), key=str)
    dot_string = nodes.get_dot_graph(leaf_nodes).to_string()
    self.WriteRenderedDotFile(dot_string)

    self.assertSameElements(
        expected_dot_graph_str.split('\n'),
        dot_string.split('\n'),
        msg='Result dot graph is:\n{}\nCache output dict keys are: {}'.format(
            dot_string, cache_output_dict.keys()))

  def test_no_data_needed(self):
    span_0_key = 'span-0'
    span_1_key = 'span-1'

    def preprocessing_fn(inputs):
      return {k: tf.identity(v) for k, v in six.iteritems(inputs)}

    input_metadata = dataset_metadata.DatasetMetadata(
        schema_utils.schema_from_feature_spec({
            'x': tf.io.FixedLenFeature([], tf.float32),
        }))
    input_data_dict = {
        span_0_key: None,
        span_1_key: None,
    }

    with _TestPipeline() as p:
      flat_data = None
      cache_dict = {
          span_0_key: {},
          span_1_key: {},
      }

      _, output_cache = (
          (flat_data, input_data_dict, cache_dict, input_metadata)
          | 'Analyze' >> tft_beam.AnalyzeDatasetWithCache(
              preprocessing_fn, pipeline=p))
      self.assertFalse(output_cache)

  def test_tf_function_fails_cache(self):

    def preprocessing_fn(inputs):

      @tf.function
      def identity(x):
        return x

      return {
          'x_mean':
              tft.mean(identity(inputs['x']), name='x') +
              tf.zeros_like(inputs['x'])
      }

    input_metadata = dataset_metadata.DatasetMetadata(
        schema_utils.schema_from_feature_spec({
            'x': tf.io.FixedLenFeature([], tf.float32),
        }))
    input_data_dict = {
        'span-0': [dict(x=-2), dict(x=4)],
    }
    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(
          list(itertools.chain(*input_data_dict.values())))

      # wrap each value in input_data_dict as a pcoll.
      input_data_pcoll_dict = {}
      for a, b in six.iteritems(input_data_dict):
        input_data_pcoll_dict[a] = p | a >> beam.Create(b)

      _, cache_output = (
          (flat_data, input_data_pcoll_dict, {}, input_metadata)
          | 'Analyze' >> tft_beam.AnalyzeDatasetWithCache(preprocessing_fn))
      _ = (
          cache_output
          | 'WriteCache' >> analyzer_cache.WriteAnalysisCacheToFS(
              p, self._cache_dir))

      del input_data_pcoll_dict

      for key in input_data_dict:
        self.assertIn(key, cache_output)
        self.assertEqual(1, len(cache_output[key]))

    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 2)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 0)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 1)
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 2)

    with _TestPipeline() as p:
      flat_data = p | 'CreateInputData' >> beam.Create(
          list(itertools.chain(*input_data_dict.values())))

      # wrap each value in input_data_dict as a pcoll.
      input_data_pcoll_dict = {}
      for a, b in six.iteritems(input_data_dict):
        input_data_pcoll_dict[a] = p | a >> beam.Create(b)

      input_cache = p | analyzer_cache.ReadAnalysisCacheFromFS(
          self._cache_dir, list(input_data_dict.keys()))

      _, second_output_cache = (
          (flat_data, input_data_pcoll_dict, input_cache, input_metadata)
          | 'AnalyzeAgain' >>
          (tft_beam.AnalyzeDatasetWithCache(preprocessing_fn)))
      _ = (
          second_output_cache
          | 'WriteCache' >> analyzer_cache.WriteAnalysisCacheToFS(
              p, self._cache_dir))

      # We expect a full output cache again because tf.function in the
      # preprocessing_fn broke that cache entry.
      for key in input_data_dict:
        self.assertIn(key, cache_output)
        self.assertEqual(1, len(cache_output[key]))

    self.assertEqual(_get_counter_value(p.metrics, 'num_instances'), 2)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_decoded'), 0)
    self.assertEqual(_get_counter_value(p.metrics, 'cache_entries_encoded'), 1)
    self.assertEqual(_get_counter_value(p.metrics, 'saved_models_created'), 2)

if __name__ == '__main__':
  tft_unit.main()
