# Lint as: python3
# Copyright 2017 Google Inc. All Rights Reserved.
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
"""Tests for tft.bucketize and tft.quantiles."""

import contextlib
import random

# GOOGLE-INITIALIZATION

import numpy as np
from six.moves import range

import tensorflow as tf
import tensorflow_transform as tft
from tensorflow_transform import analyzers
from tensorflow_transform.beam import impl as beam_impl
from tensorflow_transform.beam import tft_unit
from tensorflow_metadata.proto.v0 import schema_pb2

# pylint: disable=g-complex-comprehension


def _construct_test_bucketization_tight_sequence_parameters():
  # (test_inputs, expected_boundaries, dtype, num_buckets, num_expected_buckets)
  args = (
      ([1, 2, 3, 4], np.array([[3]], np.float32), tf.int32, 2, 2),
      ([1, 2, 3, 4], np.array([[2, 3]], np.float32), tf.int32, 3, 3),
      ([1, 2, 3, 4], np.array([[2, 3, 4]], np.float32), tf.int32, 4, 4),
      ([1, 2, 3, 4], np.array([[1, 2, 3, 4]], np.float32), tf.int32, 5, 5),
      ([1, 2, 3, 4], np.array([[1, 2, 3, 3, 4]], np.float32), tf.int32, 6, 6),
      ([1, 2, 3, 4], np.array([[1, 1, 2, 2, 3, 3, 3, 4, 4]],
                              np.float32), tf.int32, 10, 10),
  )
  return args


def _construct_test_bucketization_parameters():
  args_without_dtype = (
      (range(1, 10), [4, 7], False, None, False, False),
      (range(1, 100), [25, 50, 75], False, None, False, False),

      # The following is similar to range(1, 100) test above, except that
      # only odd numbers are in the input; so boundaries differ (26 -> 27 and
      # 76 -> 77).
      (range(1, 100, 2), [24, 50, 75], False, None, False, False),

      # Test some inversely sorted inputs, and with different strides, and
      # boundaries/buckets.
      (range(9, 0, -1), [4, 7], False, None, False, False),
      (range(19, 0, -1), [10], False, None, False, False),
      (range(99, 0, -1), [50], False, None, False, False),
      (range(99, 0, -1), [34, 67], False, None, False, False),
      (range(99, 0, -2), [33, 67], False, None, False, False),
      (range(99, 0, -1), range(10, 100, 10), False, None, False, False),

      # These tests do a random shuffle of the inputs, which must not affect the
      # boundaries (or the computed buckets).
      (range(99, 0, -1), range(10, 100, 10), True, None, False, False),
      (range(1, 100), range(10, 100, 10), True, None, False, False),

      # The following test is with multiple batches (3 batches with default
      # batch of 1000).
      (range(1, 3000), [1500], False, None, False, False),
      (range(1, 3000), [1000, 2000], False, None, False, False),

      # Test with specific error for bucket boundaries. This is same as the test
      # above with 3 batches and a single boundary, but with a stricter error
      # tolerance (0.001) than the default error (0.01).
      (range(1, 3000), [1500], False, 0.001, False, False),

      # Tests for tft.apply_buckets.
      (range(1, 100), [25, 50, 75], False, 0.00001, True, False),
      (range(1, 100), [25, 50, 75], False, 0.00001, True, True),
  )
  dtypes = (tf.int32, tf.int64, tf.float32, tf.float64, tf.double)
  return (x + (dtype,) for x in args_without_dtype for dtype in dtypes)


class BucketizeIntegrationTest(tft_unit.TransformTestCase):

  def setUp(self):
    self._context = beam_impl.Context(
        use_deep_copy_optimization=True, force_tf_compat_v1=True)
    self._context.__enter__()
    super(BucketizeIntegrationTest, self).setUp()

  def tearDown(self):
    self._context.__exit__()
    super(BucketizeIntegrationTest, self).tearDown()

  @tft_unit.parameters(
      # Test for all integral types, each type is in a separate testcase to
      # increase parallelism of test shards (and reduce test time from ~250
      # seconds to ~80 seconds)
      *_construct_test_bucketization_parameters())
  def testBucketization(self, test_inputs, expected_boundaries, do_shuffle,
                        epsilon, should_apply, is_manual_boundaries,
                        input_dtype):
    test_inputs = list(test_inputs)

    # Shuffle the input to add randomness to input generated with
    # simple range().
    if do_shuffle:
      random.shuffle(test_inputs)

    def preprocessing_fn(inputs):
      x = tf.cast(inputs['x'], input_dtype)
      num_buckets = len(expected_boundaries) + 1
      if should_apply:
        if is_manual_boundaries:
          bucket_boundaries = [expected_boundaries]
        else:
          bucket_boundaries = tft.quantiles(inputs['x'], num_buckets, epsilon)
        result = tft.apply_buckets(x, bucket_boundaries)
      else:
        result = tft.bucketize(x, num_buckets=num_buckets, epsilon=epsilon)
      return {'q_b': result}

    input_data = [{'x': [x]} for x in test_inputs]

    input_metadata = tft_unit.metadata_from_feature_spec({
        'x':
            tf.io.FixedLenFeature([1],
                                  tft_unit.canonical_numeric_dtype(input_dtype))
    })

    # Sort the input based on value, index is used to create expected_data.
    indexed_input = enumerate(test_inputs)

    sorted_list = sorted(indexed_input, key=lambda p: p[1])

    # Expected data has the same size as input, one bucket per input value.
    expected_data = [None] * len(test_inputs)
    bucket = 0
    for (index, x) in sorted_list:
      # Increment the bucket number when crossing the boundary
      if (bucket < len(expected_boundaries) and
          x >= expected_boundaries[bucket]):
        bucket += 1
      expected_data[index] = {'q_b': [bucket]}

    expected_metadata = tft_unit.metadata_from_feature_spec(
        {
            'q_b': tf.io.FixedLenFeature([1], tf.int64),
        }, {
            'q_b':
                schema_pb2.IntDomain(
                    min=0, max=len(expected_boundaries), is_categorical=True),
        })

    @contextlib.contextmanager
    def no_assert():
      yield None

    assertion = no_assert()
    if input_dtype == tf.float16:
      assertion = self.assertRaisesRegexp(
          TypeError, '.*DataType float16 not in list of allowed values.*')

    with assertion:
      self.assertAnalyzeAndTransformResults(
          input_data,
          input_metadata,
          preprocessing_fn,
          expected_data,
          expected_metadata,
          desired_batch_size=1000)

  @tft_unit.parameters(
      # Test for all integral types, each type is in a separate testcase to
      # increase parallelism of test shards (and reduce test time from ~250
      # seconds to ~80 seconds)
      *_construct_test_bucketization_parameters())
  def testBucketizationElementwise(self, test_inputs, expected_boundaries,
                                   do_shuffle, epsilon, should_apply,
                                   is_manual_boundaries, input_dtype):
    test_inputs = list(test_inputs)

    # Shuffle the input to add randomness to input generated with
    # simple range().
    if do_shuffle:
      random.shuffle(test_inputs)

    def preprocessing_fn(inputs):
      x = tf.cast(inputs['x'], input_dtype)

      num_buckets = len(expected_boundaries) + 1
      if should_apply:
        if is_manual_boundaries:
          bucket_boundaries = [
              expected_boundaries, [2 * b for b in expected_boundaries]
          ]
        else:
          bucket_boundaries = tft.quantiles(
              x, num_buckets, epsilon, reduce_instance_dims=False)
          bucket_boundaries = tf.unstack(bucket_boundaries, axis=0)

        result = []
        for i, boundaries in enumerate(bucket_boundaries):
          boundaries = tf.cast(boundaries, tf.float32)
          result.append(
              tft.apply_buckets(x[:, i], tf.expand_dims(boundaries, axis=0)))
        result = tf.stack(result, axis=1)

      else:
        result = tft.bucketize(
            x, num_buckets=num_buckets, epsilon=epsilon, elementwise=True)
      return {'q_b': result}

    input_data = [{'x': [x, 2 * x]} for x in test_inputs]

    input_metadata = tft_unit.metadata_from_feature_spec({
        'x':
            tf.io.FixedLenFeature([2],
                                  tft_unit.canonical_numeric_dtype(input_dtype))
    })

    # Sort the input based on value, index is used to create expected_data.
    sorted_list = sorted(enumerate(test_inputs), key=lambda p: p[1])

    # Expected data has the same size as input, one bucket per input value.
    expected_data = [[None, None]] * len(test_inputs)
    bucket = 0

    for (index, x) in sorted_list:
      # Increment the bucket number when crossing the boundary
      if (bucket < len(expected_boundaries) and
          x >= expected_boundaries[bucket]):
        bucket += 1
      expected_data[index] = {'q_b': [bucket, bucket]}

    expected_metadata = tft_unit.metadata_from_feature_spec(
        {
            'q_b': tf.io.FixedLenFeature([2], tf.int64),
        }, None)

    @contextlib.contextmanager
    def no_assert():
      yield None

    assertion = no_assert()
    if input_dtype == tf.float16:
      assertion = self.assertRaisesRegexp(
          TypeError, '.*DataType float16 not in list of allowed values.*')

    with assertion:
      self.assertAnalyzeAndTransformResults(
          input_data,
          input_metadata,
          preprocessing_fn,
          expected_data,
          expected_metadata,
          desired_batch_size=1000)

  def testBucketizeSparseInput(self):

    def preprocessing_fn(inputs):
      return {
          'x_bucketized':
              tft.bucketize(inputs['x'], num_buckets=3, epsilon=0.00001)
      }

    input_data = [{
        'val': [x],
        'idx0': [x % 4],
        'idx1': [x % 5]
    } for x in range(1, 10)]
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x': tf.io.SparseFeature(['idx0', 'idx1'], 'val', tf.float32, [4, 5]),
    })

    def compute_bucket(instance):
      if instance['val'][0] < 4:
        return 0
      if instance['val'][0] < 7:
        return 1
      return 2

    expected_data = [{
        'x_bucketized$sparse_values': [compute_bucket(instance)],
        'x_bucketized$sparse_indices_0': instance['idx0'],
        'x_bucketized$sparse_indices_1': instance['idx1']
    } for instance in input_data]
    self.assertAnalyzeAndTransformResults(input_data, input_metadata,
                                          preprocessing_fn, expected_data)

  # Test for all numerical types, each type is in a separate testcase to
  # increase parallelism of test shards and reduce test time.
  @tft_unit.parameters(
      (tf.int32,),
      (tf.int64,),
      (tf.float32,),
      (tf.float64,),
      (tf.double,),
      # TODO(b/64836936): Enable test after bucket inconsistency is fixed.
      # (tf.float16,)
  )
  def testQuantileBucketsWithWeights(self, input_dtype):

    def analyzer_fn(inputs):
      return {
          'q_b':
              tft.quantiles(
                  tf.cast(inputs['x'], input_dtype),
                  num_buckets=3,
                  epsilon=0.00001,
                  weights=inputs['weights'])
      }

    input_data = [{'x': [x], 'weights': [x / 100.]} for x in range(1, 3000)]
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x':
            tf.io.FixedLenFeature(
                [1], tft_unit.canonical_numeric_dtype(input_dtype)),
        'weights':
            tf.io.FixedLenFeature([1], tf.float32)
    })
    # The expected data has 2 boundaries that divides the data into 3 buckets.
    expected_outputs = {'q_b': np.array([[1732, 2449]], np.float32)}
    self.assertAnalyzerOutputs(
        input_data,
        input_metadata,
        analyzer_fn,
        expected_outputs,
        desired_batch_size=1000)

  # Test for all numerical types, each type is in a separate testcase to
  # increase parallelism of test shards and reduce test time.
  @tft_unit.parameters(
      (tf.int32,),
      (tf.int64,),
      (tf.float32,),
      (tf.float64,),
      (tf.double,),
      # TODO(b/64836936): Enable test after bucket inconsistency is fixed.
      # (tf.float16,)
  )
  def testElementwiseQuantileBucketsWithWeights(self, input_dtype):

    def analyzer_fn(inputs):
      return {
          'q_b':
              tft.quantiles(
                  tf.cast(inputs['x'], input_dtype),
                  num_buckets=3,
                  epsilon=0.00001,
                  weights=inputs['weights'],
                  reduce_instance_dims=False)
      }

    input_data = [{
        'x': [[x, 2 * x], [2 * x, x]],
        'weights': [x / 100.]
    } for x in range(1, 3000)]
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x':
            tf.io.FixedLenFeature(
                [2, 2], tft_unit.canonical_numeric_dtype(input_dtype)),
        'weights':
            tf.io.FixedLenFeature([1], tf.float32)
    })
    # The expected data has 2 boundaries that divides the data into 3 buckets.
    expected_outputs = {
        'q_b':
            np.array(
                [[[1732, 2449], [3464, 4898]], [[3464, 4898], [1732, 2449]]],
                np.float32)
    }
    self.assertAnalyzerOutputs(
        input_data,
        input_metadata,
        analyzer_fn,
        expected_outputs,
        desired_batch_size=1000)

  # Test for all numerical types, each type is in a separate testcase to
  # increase parallelism of test shards and reduce test time.
  @tft_unit.parameters(
      (tf.int32,),
      (tf.int64,),
      (tf.float32,),
      (tf.float64,),
      (tf.double,),
      # TODO(b/64836936): Enable test after bucket inconsistency is fixed.
      # (tf.float16,)
  )
  def testQuantileBuckets(self, input_dtype):

    def analyzer_fn(inputs):
      return {
          'q_b':
              tft.quantiles(
                  tf.cast(inputs['x'], input_dtype),
                  num_buckets=3,
                  epsilon=0.00001)
      }

    # NOTE: We force 3 batches: data has 3000 elements and we request a batch
    # size of 1000.
    input_data = [{'x': [x]} for x in range(1, 3000)]
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x':
            tf.io.FixedLenFeature([1],
                                  tft_unit.canonical_numeric_dtype(input_dtype))
    })
    # The expected data has 2 boundaries that divides the data into 3 buckets.
    expected_outputs = {'q_b': np.array([[1000, 2000]], np.float32)}
    self.assertAnalyzerOutputs(
        input_data,
        input_metadata,
        analyzer_fn,
        expected_outputs,
        desired_batch_size=1000)

  def testQuantilesPerKey(self):

    def analyzer_fn(inputs):
      key_vocab, q_b, scale_factor_per_key, shift_per_key, num_buckets = (
          analyzers._quantiles_per_key(
              inputs['x'], inputs['key'], num_buckets=3, epsilon=0.00001))
      return {
          'key_vocab': key_vocab,
          'q_b': q_b,
          'scale_factor_per_key': scale_factor_per_key,
          'shift_per_key': shift_per_key,
          'num_buckets': num_buckets,
      }

    # NOTE: We force 10 batches: data has 100 elements and we request a batch
    # size of 10.
    input_data = [{
        'x': [x],
        'key': 'a' if x < 50 else 'b'
    } for x in range(1, 100)]
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x': tf.io.FixedLenFeature([1], tf.int64),
        'key': tf.io.FixedLenFeature([], tf.string)
    })
    # The expected data has 2 boundaries that divides the data into 3 buckets.
    expected_outputs = {
        'key_vocab': np.array([b'a', b'b'], np.object),
        'q_b': np.array([0., 1., 2.], np.float32),
        'scale_factor_per_key': np.array([0.0625, 0.05882353], np.float32),
        'shift_per_key': np.array([-1.0625, -2.88235283], np.float32),
        'num_buckets': np.array(3, np.int64),
    }
    self.assertAnalyzerOutputs(
        input_data,
        input_metadata,
        analyzer_fn,
        expected_outputs,
        desired_batch_size=10)

  def testBucketizePerKey(self):

    def preprocessing_fn(inputs):
      x_bucketized = tft.bucketize_per_key(
          inputs['x'], inputs['key'], num_buckets=3, epsilon=0.00001)
      return {'x': inputs['x'], 'x_bucketized': x_bucketized}

    # NOTE: We force 10 batches: data has 100 elements and we request a batch
    # size of 10.
    input_data = [{
        'x': x,
        'key': 'a' if x < 50 else 'b'
    } for x in range(1, 100)]
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x': tf.io.FixedLenFeature([], tf.float32),
        'key': tf.io.FixedLenFeature([], tf.string)
    })

    def compute_bucket(instance):
      if instance['key'] == 'a':
        if instance['x'] < 17:
          return 0
        elif instance['x'] < 33:
          return 1
        else:
          return 2
      else:
        if instance['x'] < 66:
          return 0
        elif instance['x'] < 83:
          return 1
        else:
          return 2

    expected_data = [{
        'x_bucketized': compute_bucket(instance),
        'x': instance['x']
    } for instance in input_data]
    expected_metadata = tft_unit.metadata_from_feature_spec(
        {
            'x': tf.io.FixedLenFeature([], tf.float32),
            'x_bucketized': tf.io.FixedLenFeature([], tf.int64),
        }, {
            'x_bucketized':
                schema_pb2.IntDomain(min=0, max=2, is_categorical=True),
        })
    self.assertAnalyzeAndTransformResults(
        input_data,
        input_metadata,
        preprocessing_fn,
        expected_data,
        expected_metadata,
        desired_batch_size=10)

  def testBucketizePerKeyWithInfrequentKeys(self):

    def preprocessing_fn(inputs):
      x_bucketized = tft.bucketize_per_key(
          inputs['x'], inputs['key'], num_buckets=4, epsilon=0.00001)
      return {'x': inputs['x'], 'x_bucketized': x_bucketized}

    input_data = [
        {'x': [], 'key': []},
        {'x': [5, 6], 'key': ['a', 'a']},
        {'x': [7], 'key': ['a']},
        {'x': [12], 'key': ['b']},
        {'x': [13], 'key': ['b']},
        {'x': [15], 'key': ['c']},
        {'x': [2], 'key': ['d']},
        {'x': [4], 'key': ['d']},
        {'x': [6], 'key': ['d']},
        {'x': [8], 'key': ['d']},
        {'x': [2], 'key': ['e']},
        {'x': [4], 'key': ['e']},
        {'x': [6], 'key': ['e']},
        {'x': [8], 'key': ['e']},
        {'x': [10], 'key': ['e']},
        {'x': [11], 'key': ['e']},
        {'x': [12], 'key': ['e']},
        {'x': [13], 'key': ['e']}
    ]  # pyformat: disable
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x': tf.io.VarLenFeature(tf.float32),
        'key': tf.io.VarLenFeature(tf.string)
    })
    expected_data = [
        {'x': [], 'x_bucketized': []},
        {'x': [5, 6], 'x_bucketized': [1, 2]},
        {'x': [7], 'x_bucketized': [3]},
        {'x': [12], 'x_bucketized': [1]},
        {'x': [13], 'x_bucketized': [3]},
        {'x': [15], 'x_bucketized': [1]},
        {'x': [2], 'x_bucketized': [0]},
        {'x': [4], 'x_bucketized': [1]},
        {'x': [6], 'x_bucketized': [2]},
        {'x': [8], 'x_bucketized': [3]},
        {'x': [2], 'x_bucketized': [0]},
        {'x': [4], 'x_bucketized': [0]},
        {'x': [6], 'x_bucketized': [1]},
        {'x': [8], 'x_bucketized': [1]},
        {'x': [10], 'x_bucketized': [2]},
        {'x': [11], 'x_bucketized': [2]},
        {'x': [12], 'x_bucketized': [3]},
        {'x': [13], 'x_bucketized': [2]}
    ]  # pyformat: disable
    expected_metadata = tft_unit.metadata_from_feature_spec(
        {
            'x': tf.io.VarLenFeature(tf.float32),
            'x_bucketized': tf.io.VarLenFeature(tf.int64),
        }, {
            'x_bucketized':
                schema_pb2.IntDomain(min=0, max=3, is_categorical=True),
        })
    self.assertAnalyzeAndTransformResults(
        input_data,
        input_metadata,
        preprocessing_fn,
        expected_data,
        expected_metadata,
        desired_batch_size=10)

  def testBucketizePerKeySparse(self):

    def preprocessing_fn(inputs):
      x_bucketized = tft.bucketize_per_key(
          inputs['x'], inputs['key'], num_buckets=3, epsilon=0.00001)
      return {'x_bucketized': x_bucketized}

    # NOTE: We force 10 batches: data has 100 elements and we request a batch
    # size of 10.
    input_data = [{
        'x': [x],
        'key': ['a'] if x < 50 else ['b']
    } for x in range(1, 100)]
    input_metadata = tft_unit.metadata_from_feature_spec({
        'x': tf.io.VarLenFeature(tf.float32),
        'key': tf.io.VarLenFeature(tf.string)
    })

    def compute_bucket(instance):
      if instance['key'][0] == 'a':
        if instance['x'][0] < 17:
          return 0
        elif instance['x'][0] < 33:
          return 1
        else:
          return 2
      else:
        if instance['x'][0] < 66:
          return 0
        elif instance['x'][0] < 83:
          return 1
        else:
          return 2

    expected_data = [{
        'x_bucketized': [compute_bucket(instance)]
    } for instance in input_data]
    expected_metadata = tft_unit.metadata_from_feature_spec(
        {
            'x_bucketized': tf.io.VarLenFeature(tf.int64),
        }, {
            'x_bucketized':
                schema_pb2.IntDomain(min=0, max=2, is_categorical=True),
        })
    self.assertAnalyzeAndTransformResults(
        input_data,
        input_metadata,
        preprocessing_fn,
        expected_data,
        expected_metadata,
        desired_batch_size=10)

  def _assert_quantile_boundaries(self,
                                  test_inputs,
                                  expected_boundaries,
                                  input_dtype,
                                  num_buckets=None,
                                  num_expected_buckets=None):

    if not num_buckets:
      num_buckets = len(expected_boundaries) + 1
    if not num_expected_buckets:
      num_expected_buckets = num_buckets

    def analyzer_fn(inputs):
      x = tf.cast(inputs['x'], input_dtype)
      return {'q_b': tft.quantiles(x, num_buckets, epsilon=0.0001)}

    input_data = [{'x': [x]} for x in test_inputs]

    input_metadata = tft_unit.metadata_from_feature_spec({
        'x':
            tf.io.FixedLenFeature([1],
                                  tft_unit.canonical_numeric_dtype(input_dtype))
    })

    expected_data = {'q_b': expected_boundaries}

    self.assertAnalyzerOutputs(
        input_data,
        input_metadata,
        analyzer_fn,
        expected_data,
        desired_batch_size=1000)

  @tft_unit.parameters(
      *_construct_test_bucketization_tight_sequence_parameters())
  def testBucketizationForTightSequence(self, test_inputs, expected_boundaries,
                                        dtype, num_buckets,
                                        num_expected_buckets):
    self._assert_quantile_boundaries(
        test_inputs,
        expected_boundaries,
        dtype,
        num_buckets=num_buckets,
        num_expected_buckets=num_expected_buckets)

  def testBucketizationEqualDistributionInSequence(self):
    # Input pattern is of the form [1, 1, 1, ..., 2, 2, 2, ..., 3, 3, 3, ...]
    inputs = []
    for i in range(1, 101):
      inputs += [i] * 100
    # Expect 100 equally spaced buckets.
    expected_buckets = np.expand_dims(
        np.arange(1, 101, dtype=np.float32), axis=0)
    self._assert_quantile_boundaries(
        inputs, expected_buckets, tf.int32, num_buckets=101)

  def testBucketizationEqualDistributionInterleaved(self):
    # Input pattern is of the form [1, 2, 3, ..., 1, 2, 3, ..., 1, 2, 3, ...]
    sequence = range(1, 101)
    inputs = []
    for _ in range(1, 101):
      inputs += sequence
    # Expect 100 equally spaced buckets.
    expected_buckets = np.expand_dims(
        np.arange(1, 101, dtype=np.float32), axis=0)
    self._assert_quantile_boundaries(
        inputs, expected_buckets, tf.int32, num_buckets=101)

  def testBucketizationSpecificDistribution(self):
    # Distribution of input values.
    # This distribution is taken from one of the user pipelines.
    dist = (
        # Format: ((<min-value-in-range>, <max-value-in-range>), num-values)
        ((0.51, 0.67), 4013),
        ((0.67, 0.84), 2321),
        ((0.84, 1.01), 7145),
        ((1.01, 1.17), 64524),
        ((1.17, 1.34), 42886),
        ((1.34, 1.51), 154809),
        ((1.51, 1.67), 382678),
        ((1.67, 1.84), 582744),
        ((1.84, 2.01), 252221),
        ((2.01, 2.17), 7299))

    inputs = []
    for (mn, mx), num in dist:
      step = (mx - mn) / 100
      for ix in range(num // 100):
        inputs += [mn + (ix * step)]

    expected_boundaries = np.array([[2.3084, 3.5638, 5.0972, 7.07]],
                                   dtype=np.float32)

    self._assert_quantile_boundaries(
        inputs, expected_boundaries, tf.float32, num_buckets=5)


if __name__ == '__main__':
  tft_unit.main()
