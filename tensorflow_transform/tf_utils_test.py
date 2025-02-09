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
"""Tests for tensorflow_transform.tf_utils."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

# GOOGLE-INITIALIZATION
import numpy as np
import tensorflow as tf

from tensorflow_transform import tf_utils
from tensorflow_transform import test_case


class _SparseTensorSpec(object):

  def __init__(self, shape, dtype):
    self._shape = shape
    self._dtype = dtype

if not hasattr(tf, 'SparseTensorSpec'):
  tf.SparseTensorSpec = _SparseTensorSpec


class TFUtilsTest(test_case.TransformTestCase):

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='rank1',
          x=['a', 'b', 'a'],
          weights=[1, 1, 2],
          expected_unique_x=[b'a', b'b'],
          expected_summed_weights_per_x=[3, 1]),
      dict(
          testcase_name='rank2',
          x=[['a', 'b', 'a'], ['b', 'a', 'b']],
          weights=[[1, 2, 1], [1, 2, 2]],
          expected_unique_x=[b'a', b'b'],
          expected_summed_weights_per_x=[4, 5]),
      dict(
          testcase_name='rank3',
          x=[[['a', 'b', 'a'], ['b', 'a', 'b']],
             [['a', 'b', 'a'], ['b', 'a', 'b']]],
          weights=[[[1, 1, 2], [1, 2, 1]], [[1, 2, 1], [1, 2, 1]]],
          expected_unique_x=[b'a', b'b'],
          expected_summed_weights_per_x=[9, 7]),
  ]))
  def test_reduce_batch_weighted_counts(
      self, x, weights, expected_unique_x, expected_summed_weights_per_x,
      function_handler):
    input_signature = [tf.TensorSpec(None, tf.string),
                       tf.TensorSpec(None, tf.float32)]
    @function_handler(input_signature=input_signature)
    def _reduce_batch_weighted_counts(x, weights):
      (unique_x, summed_weights_per_x, summed_positive_per_x_and_y,
       counts_per_x) = tf_utils.reduce_batch_weighted_counts(x, weights)
      self.assertIsNone(summed_positive_per_x_and_y)
      self.assertIsNone(counts_per_x)
      return unique_x, summed_weights_per_x

    unique_x, summed_weights_per_x = _reduce_batch_weighted_counts(x, weights)

    self.assertAllEqual(unique_x,
                        expected_unique_x)
    self.assertAllEqual(summed_weights_per_x,
                        expected_summed_weights_per_x)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='rank1',
          x=['a', 'b', 'a'],
          expected_unique_x=[b'a', b'b', b'a']),
      dict(
          testcase_name='rank2',
          x=[['a', 'b', 'a'], ['b', 'a', 'b']],
          expected_unique_x=[b'a', b'b', b'a', b'b', b'a', b'b']),
      dict(
          testcase_name='rank3',
          x=[[['a', 'b', 'a'], ['b', 'a', 'b']],
             [['a', 'b', 'a'], ['b', 'a', 'b']]],
          expected_unique_x=[b'a', b'b', b'a', b'b', b'a', b'b', b'a', b'b',
                             b'a', b'b', b'a', b'b']),
  ]))
  def test_reduce_batch_weighted_counts_weights_none(
      self, x, expected_unique_x, function_handler):
    input_signature = [tf.TensorSpec(None, tf.string)]
    @function_handler(input_signature=input_signature)
    def _reduce_batch_weighted_counts(x):
      (unique_x, summed_weights_per_x, summed_positive_per_x_and_y,
       counts_per_x) = tf_utils.reduce_batch_weighted_counts(x)
      self.assertIsNone(summed_weights_per_x)
      self.assertIsNone(summed_positive_per_x_and_y)
      self.assertIsNone(counts_per_x)
      return unique_x

    unique_x = _reduce_batch_weighted_counts(x)

    self.assertAllEqual(unique_x,
                        expected_unique_x)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='rank1_with_weights_and_binary_y',
          x=['a', 'b', 'a'],
          weights=[1, 1, 2],
          y=[0, 1, 1],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [3, 1, 4],
              [[1, 2], [0, 1], [1, 3]], [2, 1, 3])),
      dict(
          testcase_name='rank1_with_weights_and_multi_class_y',
          x=['a', 'b', 'a', 'a'],
          weights=[1, 1, 2, 2],
          y=[0, 2, 1, 1],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [5, 1, 6],
              [[1, 4, 0], [0, 0, 1], [1, 4, 1]], [3, 1, 4])),
      dict(
          testcase_name='rank1_with_weights_and_missing_y_values',
          x=['a', 'b', 'a', 'a'],
          weights=[1, 1, 2, 2],
          y=[3, 5, 6, 6],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [5, 1, 6],
              [[0, 0, 0, 1, 0, 0, 4], [0, 0, 0, 0, 0, 1, 0],
               [0, 0, 0, 1, 0, 1, 4]],
              [3, 1, 4])),
      dict(
          testcase_name='rank2_with_weights_and_binary_y',
          x=[['a', 'b', 'a'], ['b', 'a', 'b']],
          weights=[[1, 2, 1], [1, 2, 2]],
          y=[[1, 0, 1], [1, 0, 0]],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [4, 5, 9],
              [[2, 2], [4, 1], [6, 3]], [3, 3, 6])),
      dict(
          testcase_name='rank3_with_weights_and_binary_y',
          x=[[['a', 'b', 'a'], ['b', 'a', 'b']],
             [['a', 'b', 'a'], ['b', 'a', 'b']]],
          weights=[[[1, 1, 2], [1, 2, 1]], [[1, 2, 1], [1, 2, 1]]],
          y=[[[1, 1, 0], [1, 0, 1]], [[1, 0, 1], [1, 0, 1]]],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [9, 7, 16],
              [[6, 3], [2, 5], [8, 8]], [6, 6, 12])),
  ]))
  def test_reduce_batch_coocurrences(self, x, weights, y, expected_result,
                                     function_handler):
    input_signature = [tf.TensorSpec(None, tf.string),
                       tf.TensorSpec(None, tf.int64),
                       tf.TensorSpec(None, tf.int64)]

    @function_handler(input_signature=input_signature)
    def _reduce_batch_weighted_cooccurrences(x, y, weights):
      return tf_utils.reduce_batch_weighted_cooccurrences(x, y, weights)

    result = _reduce_batch_weighted_cooccurrences(x, y, weights)

    self.assertAllEqual(result.unique_x,
                        expected_result.unique_x)
    self.assertAllEqual(result.summed_weights_per_x,
                        expected_result.summed_weights_per_x)
    self.assertAllEqual(result.summed_positive_per_x_and_y,
                        expected_result.summed_positive_per_x_and_y)
    self.assertAllEqual(result.counts_per_x,
                        expected_result.counts_per_x)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='rank1_with_binary_y',
          x=['a', 'b', 'a'],
          y=[0, 1, 1],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [2, 1, 3],
              [[1, 1], [0, 1], [1, 2]], [2, 1, 3]),
          input_signature=[tf.TensorSpec(None, tf.string),
                           tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='rank1_with_multi_class_y',
          x=['yes', 'no', 'yes', 'maybe', 'yes'],
          y=[1, 1, 0, 2, 3],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'yes', b'no', b'maybe', b'global_y_count_sentinel'],
              [3, 1, 1, 5],
              [[1, 1, 0, 1], [0, 1, 0, 0], [0, 0, 1, 0], [1, 2, 1, 1]],
              [3, 1, 1, 5]),
          input_signature=[tf.TensorSpec(None, tf.string),
                           tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='rank2_with_binary_y',
          x=[['a', 'b', 'a'], ['b', 'a', 'b']],
          y=[[1, 0, 1], [1, 0, 0]],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [3, 3, 6],
              [[1, 2], [2, 1], [3, 3]], [3, 3, 6]),
          input_signature=[tf.TensorSpec(None, tf.string),
                           tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='rank2_with_missing_y_values',
          x=[['a', 'b', 'a'], ['b', 'a', 'b']],
          y=[[2, 0, 2], [2, 0, 0]],
          # The label 1 isn't in the batch but it will have a position (with
          # weights of 0) in the resulting array.
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [3, 3, 6],
              [[1, 0, 2], [2, 0, 1], [3, 0, 3]], [3, 3, 6]),
          input_signature=[tf.TensorSpec(None, tf.string),
                           tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='rank2_with_multi_class_y',
          x=[['a', 'b', 'a'], ['b', 'a', 'b']],
          y=[[1, 0, 1], [1, 0, 2]],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [3, 3, 6],
              [[1, 2, 0], [1, 1, 1], [2, 3, 1]], [3, 3, 6]),
          input_signature=[tf.TensorSpec(None, tf.string),
                           tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='rank3_with_binary_y',
          x=[[['a', 'b', 'a'], ['b', 'a', 'b']],
             [['a', 'b', 'a'], ['b', 'a', 'b']]],
          y=[[[1, 1, 0], [1, 0, 1]], [[1, 0, 1], [1, 0, 1]]],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [6, 6, 12],
              [[3, 3], [1, 5], [4, 8]], [6, 6, 12]),
          input_signature=[tf.TensorSpec(None, tf.string),
                           tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='sparse',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [2, 1]], values=['a', 'b'], dense_shape=[4, 2]),
          y=[0, 1, 0, 0],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'a', b'b', b'global_y_count_sentinel'], [1, 1, 4],
              [[1, 0], [1, 0], [3, 1]], [1, 1, 4]),
          input_signature=[tf.SparseTensorSpec([None, 2], tf.string),
                           tf.TensorSpec([None], tf.int64)]),
      dict(
          testcase_name='empty_sparse',
          x=tf.compat.v1.SparseTensorValue(
              indices=np.empty([0, 2]), values=[], dense_shape=[4, 2]),
          y=[1, 0, 1, 1],
          expected_result=tf_utils.ReducedBatchWeightedCounts(
              [b'global_y_count_sentinel'], [4], [[1, 3]], [4]),
          input_signature=[tf.SparseTensorSpec([None, 2], tf.string),
                           tf.TensorSpec([None], tf.int64)]),
  ]))
  def test_reduce_batch_coocurrences_no_weights(
      self, x, y, expected_result, input_signature, function_handler):
    @function_handler(input_signature=input_signature)
    def _reduce_batch_weighted_cooccurrences_no_weights(x, y):
      return tf_utils.reduce_batch_weighted_cooccurrences(x, y)

    result = _reduce_batch_weighted_cooccurrences_no_weights(x, y)

    self.assertAllEqual(result.unique_x,
                        expected_result.unique_x)
    self.assertAllEqual(result.summed_weights_per_x,
                        expected_result.summed_weights_per_x)
    self.assertAllEqual(result.summed_positive_per_x_and_y,
                        expected_result.summed_positive_per_x_and_y)
    self.assertAllEqual(result.counts_per_x,
                        expected_result.counts_per_x)

  @test_case.parameters(
      ([[1], [2]], [[1], [2], [3]], None, None, tf.errors.InvalidArgumentError,
       'Condition x == y did not hold element-wise:'),
      ([[1], [2], [3]], [[1], [2], [3]], [None, None], [None], ValueError,
       r'Shapes \(None, None\) and \(None,\) are incompatible'),
  )
  def test_same_shape_exceptions(self, x_input, y_input, x_shape, y_shape,
                                 exception_cls, error_string):

    with tf.compat.v1.Graph().as_default():
      x = tf.compat.v1.placeholder(tf.int32, x_shape)
      y = tf.compat.v1.placeholder(tf.int32, y_shape)
      with tf.compat.v1.Session() as sess:
        with self.assertRaisesRegexp(exception_cls, error_string):
          sess.run(tf_utils.assert_same_shape(x, y), {x: x_input, y: y_input})

  @test_case.named_parameters(test_case.FUNCTION_HANDLERS)
  def test_same_shape(self, function_handler):
    input_signature = [tf.TensorSpec(None, tf.int64),
                       tf.TensorSpec(None, tf.int64)]

    @function_handler(input_signature=input_signature)
    def _assert_shape(x, y):
      x_return, _ = tf_utils.assert_same_shape(x, y)
      return x_return

    input_list = [[1], [2], [3]]
    x_return = _assert_shape(input_list, input_list)
    self.assertAllEqual(x_return, input_list)

  def test_lookup_key(self):
    with tf.compat.v1.Graph().as_default():
      keys = tf.constant(['a', 'a', 'a', 'b', 'b', 'b', 'b'])
      key_vocab = tf.constant(['a', 'b'])
      key_indices = tf_utils.lookup_key(keys, key_vocab)
      with self.test_session() as sess:
        sess.run(tf.compat.v1.tables_initializer())
        output = sess.run(key_indices)
        self.assertAllEqual([0, 0, 0, 1, 1, 1, 1], output)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='dense',
          x=[[[1], [2]], [[1], [2]]],
          expected_result=4,
          reduce_instance_dims=True,
          input_signature=[tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='dense_elementwise',
          x=[[[1], [2]], [[1], [2]]],
          expected_result=[[2], [2]],
          reduce_instance_dims=False,
          input_signature=[tf.TensorSpec(None, tf.int64)]),
      dict(
          testcase_name='sparse',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0, 0], [0, 2, 0], [1, 1, 0], [1, 2, 0]],
              values=[1., 2., 3., 4.],
              dense_shape=[2, 4, 1]),
          expected_result=4,
          reduce_instance_dims=True,
          input_signature=[
              tf.SparseTensorSpec([None, 4, 1], tf.float32)
          ]),
      dict(
          testcase_name='sparse_elementwise',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0, 0], [0, 2, 0], [1, 1, 0], [1, 2, 0]],
              values=[1., 2., 3., 4.],
              dense_shape=[2, 4, 1]),
          expected_result=[[1], [1], [2], [0]],
          reduce_instance_dims=False,
          input_signature=[
              tf.SparseTensorSpec([None, 4, 1], tf.float32)
          ]),
  ]))
  def test_reduce_batch_count(
      self, x, input_signature, expected_result, reduce_instance_dims,
      function_handler):

    @function_handler(input_signature=input_signature)
    def _reduce_batch_count(x):
      return tf_utils.reduce_batch_count(
          x, reduce_instance_dims=reduce_instance_dims)

    result = _reduce_batch_count(x)
    self.assertAllEqual(result, expected_result)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='dense',
          x=[[[1], [2]], [[3], [4]]],
          expected_count=4,
          expected_mean=2.5,
          expected_var=1.25,
          reduce_instance_dims=True,
          input_signature=[tf.TensorSpec(None, tf.float32)]),
      dict(
          testcase_name='dense_elementwise',
          x=[[[1], [2]], [[3], [4]]],
          expected_count=[[2.], [2.]],
          expected_mean=[[2.], [3.]],
          expected_var=[[1.], [1.]],
          reduce_instance_dims=False,
          input_signature=[tf.TensorSpec(None, tf.float32)]),
      dict(
          testcase_name='sparse',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 2], [1, 1], [1, 2]],
              values=[1., 2., 3., 4.],
              dense_shape=[2, 4]),
          expected_count=4,
          expected_mean=2.5,
          expected_var=1.25,
          reduce_instance_dims=True,
          input_signature=[
              tf.SparseTensorSpec([None, 4], tf.float32)
          ]),
      dict(
          testcase_name='sparse_elementwise',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 3], [1, 1], [1, 3]],
              values=[1., 2., 3., 4.],
              dense_shape=[2, 5]),
          expected_count=[1.0, 1.0, 0.0, 2.0, 0.0],
          expected_mean=[1.0, 3.0, 0.0, 3.0, 0.0],
          expected_var=[0.0, 0.0, 0.0, 1.0, 0.0],
          reduce_instance_dims=False,
          input_signature=[
              tf.SparseTensorSpec([None, 5], tf.float32)
          ]),
  ]))
  def test_reduce_batch_count_mean_and_var(
      self, x, input_signature, expected_count, expected_mean, expected_var,
      reduce_instance_dims, function_handler):

    @function_handler(input_signature=input_signature)
    def _reduce_batch_count_mean_and_var(x):
      return tf_utils.reduce_batch_count_mean_and_var(
          x, reduce_instance_dims=reduce_instance_dims)

    count, mean, var = _reduce_batch_count_mean_and_var(x)
    self.assertAllEqual(count, expected_count)
    self.assertAllEqual(mean, expected_mean)
    self.assertAllEqual(var, expected_var)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='dense',
          x=[[1], [2], [3], [4], [4]],
          key=['a', 'a', 'a', 'b', 'a'],
          expected_key_vocab=[b'a', b'b'],
          expected_count=[4., 1.],
          expected_mean=[2.5, 4.],
          expected_var=[1.25, 0.],
          reduce_instance_dims=True,
          input_signature=[tf.TensorSpec([None, 1], tf.float32),
                           tf.TensorSpec([None], tf.string)]),
      dict(
          testcase_name='dense_elementwise',
          x=[[1, 2], [3, 4], [1, 2]],
          key=['a', 'a', 'b'],
          expected_key_vocab=[b'a', b'b'],
          expected_count=[[2., 2.], [1., 1.]],
          expected_mean=[[2., 3.], [1., 2.]],
          expected_var=[[1., 1.], [0., 0.]],
          reduce_instance_dims=False,
          input_signature=[tf.TensorSpec([None, 2], tf.float32),
                           tf.TensorSpec([None], tf.string)]),
      dict(
          testcase_name='sparse',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 2], [1, 1], [1, 2], [2, 3]],
              values=[1., 2., 3., 4., 4.],
              dense_shape=[3, 4]),
          key=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 2], [1, 1], [1, 2], [2, 3]],
              values=['a', 'a', 'a', 'a', 'b'],
              dense_shape=[3, 4]),
          expected_key_vocab=[b'a', b'b'],
          expected_count=[4, 1],
          expected_mean=[2.5, 4],
          expected_var=[1.25, 0],
          reduce_instance_dims=True,
          input_signature=[tf.SparseTensorSpec([None, 4], tf.float32),
                           tf.SparseTensorSpec([None, 4], tf.string)]),
      dict(
          testcase_name='sparse_x_dense_key',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 2], [1, 1], [1, 2], [2, 3]],
              values=[1., 2., 3., 4., 4.],
              dense_shape=[3, 4]),
          key=['a', 'a', 'b'],
          expected_key_vocab=[b'a', b'b'],
          expected_count=[4, 1],
          expected_mean=[2.5, 4],
          expected_var=[1.25, 0],
          reduce_instance_dims=True,
          input_signature=[tf.SparseTensorSpec([None, 4], tf.float32),
                           tf.TensorSpec([None], tf.string)]),
  ]))
  def test_reduce_batch_count_mean_and_var_per_key(
      self, x, key, input_signature, expected_key_vocab, expected_count,
      expected_mean, expected_var, reduce_instance_dims, function_handler):

    @function_handler(input_signature=input_signature)
    def _reduce_batch_count_mean_and_var_per_key(x, key):
      return tf_utils.reduce_batch_count_mean_and_var_per_key(
          x, key, reduce_instance_dims=reduce_instance_dims)

    key_vocab, count, mean, var = _reduce_batch_count_mean_and_var_per_key(
        x, key)

    self.assertAllEqual(key_vocab, expected_key_vocab)
    self.assertAllEqual(count, expected_count)
    self.assertAllEqual(mean, expected_mean)
    self.assertAllEqual(var, expected_var)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='sparse',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 1], [0, 2]],
              values=[3, 2, -1],
              dense_shape=[1, 5]),
          expected_x_minus_min=1,
          expected_x_max=3,
          reduce_instance_dims=True,
          input_signature=[tf.SparseTensorSpec([None, None], tf.int64)]),
      dict(
          testcase_name='float',
          x=[[1, 5, 2]],
          expected_x_minus_min=-1,
          expected_x_max=5,
          reduce_instance_dims=True,
          input_signature=[tf.TensorSpec([None, None], tf.float32)]),
      dict(
          testcase_name='sparse_float_elementwise',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 1], [1, 0]],
              values=[3, 2, -1],
              dense_shape=[2, 3]),
          expected_x_minus_min=[1, -2, np.nan],
          expected_x_max=[3, 2, np.nan],
          reduce_instance_dims=False,
          input_signature=[tf.SparseTensorSpec([None, None], tf.float32)]),
      dict(
          testcase_name='float_elementwise',
          x=[[1, 5, 2], [2, 3, 4]],
          reduce_instance_dims=False,
          expected_x_minus_min=[-1, -3, -2],
          expected_x_max=[2, 5, 4],
          input_signature=[tf.TensorSpec([None, None], tf.float32)]),
      dict(
          testcase_name='sparse_int64_elementwise',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 1], [1, 0]],
              values=[3, 2, -1],
              dense_shape=[2, 3]),
          reduce_instance_dims=False,
          expected_x_minus_min=[1, -2, tf.int64.min + 1],
          expected_x_max=[3, 2, tf.int64.min + 1],
          input_signature=[tf.SparseTensorSpec([None, None], tf.int64)]),
      dict(
          testcase_name='sparse_int32_elementwise',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 1], [1, 0]],
              values=[3, 2, -1],
              dense_shape=[2, 3]),
          reduce_instance_dims=False,
          expected_x_minus_min=[1, -2, tf.int32.min + 1],
          expected_x_max=[3, 2, tf.int32.min + 1],
          input_signature=[tf.SparseTensorSpec([None, None], tf.int32)]),
      dict(
          testcase_name='sparse_float64_elementwise',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 1], [1, 0]],
              values=[3, 2, -1],
              dense_shape=[2, 3]),
          reduce_instance_dims=False,
          expected_x_minus_min=[1, -2, np.nan],
          expected_x_max=[3, 2, np.nan],
          input_signature=[tf.SparseTensorSpec([None, None], tf.float64)]),
      dict(
          testcase_name='sparse_float32_elementwise',
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 1], [1, 0]],
              values=[3, 2, -1],
              dense_shape=[2, 3]),
          reduce_instance_dims=False,
          expected_x_minus_min=[1, -2, np.nan],
          expected_x_max=[3, 2, np.nan],
          input_signature=[tf.SparseTensorSpec([None, None], tf.float32)]),
  ]))
  def test_reduce_batch_minus_min_and_max(
      self, x, expected_x_minus_min, expected_x_max, reduce_instance_dims,
      input_signature, function_handler):

    @function_handler(input_signature=input_signature)
    def _reduce_batch_minus_min_and_max(x):
      return tf_utils.reduce_batch_minus_min_and_max(
          x, reduce_instance_dims=reduce_instance_dims)

    x_minus_min, x_max = _reduce_batch_minus_min_and_max(x)

    self.assertAllEqual(x_minus_min, expected_x_minus_min)
    self.assertAllEqual(x_max, expected_x_max)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='sparse',
          input_signature=[tf.SparseTensorSpec([None, None], tf.int64)],
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [1, 1], [2, 2], [3, 1]],
              values=[3, 2, -1, 3],
              dense_shape=[4, 5]),
          expected_x_minus_min=[1, -3],
          expected_x_max=[3, 3]),
      dict(
          testcase_name='float',
          input_signature=[tf.TensorSpec([None, None], tf.float32)],
          x=[[1], [5], [2], [3]],
          expected_x_minus_min=[-1, -3],
          expected_x_max=[5, 3]),
      dict(
          testcase_name='float3dims',
          input_signature=[tf.TensorSpec([None, None, None], tf.float32)],
          x=[[[1, 5], [1, 1]],
             [[5, 1], [5, 5]],
             [[2, 2], [2, 5]],
             [[3, -3], [3, 3]]],
          expected_x_minus_min=[-1, 3],
          expected_x_max=[5, 3]),
  ]))
  def test_reduce_batch_minus_min_and_max_per_key(
      self, x, input_signature, expected_x_minus_min, expected_x_max,
      function_handler):
    key = ['a', 'a', 'a', 'b']
    input_signature = input_signature + [tf.TensorSpec([None], tf.string)]
    expected_key_vocab = [b'a', b'b']

    @function_handler(input_signature=input_signature)
    def _reduce_batch_minus_min_and_max_per_key(x, key):
      return tf_utils.reduce_batch_minus_min_and_max_per_key(x, key)

    key_vocab, x_minus_min, x_max = _reduce_batch_minus_min_and_max_per_key(
        x, key)

    self.assertAllEqual(key_vocab, expected_key_vocab)
    self.assertAllEqual(x_minus_min, expected_x_minus_min)
    self.assertAllEqual(x_max, expected_x_max)

  def test_sparse_indices(self):
    exception_cls = tf.errors.InvalidArgumentError
    error_string = 'Condition x == y did not hold element-wise:'
    value = tf.compat.v1.SparseTensorValue(
        indices=[[0, 0], [1, 1], [2, 2], [3, 1]],
        values=[3, 2, -1, 3],
        dense_shape=[4, 5])
    key_value = tf.compat.v1.SparseTensorValue(
        indices=[[0, 0], [1, 2], [2, 2], [3, 1]],
        values=['a', 'a', 'a', 'b'],
        dense_shape=[4, 5])
    with tf.compat.v1.Graph().as_default():
      x = tf.compat.v1.sparse_placeholder(tf.int64, shape=[None, None])
      key = tf.compat.v1.sparse_placeholder(tf.string, shape=[None, None])
      with tf.compat.v1.Session() as sess:
        with self.assertRaisesRegexp(exception_cls, error_string):
          sess.run(tf_utils.reduce_batch_minus_min_and_max_per_key(x, key),
                   feed_dict={x: value, key: key_value})

  def test_convert_sparse_indices(self):
    exception_cls = tf.errors.InvalidArgumentError
    error_string = 'Condition x == y did not hold element-wise:'
    sparse = tf.SparseTensor(
        indices=[[0, 0], [1, 1], [2, 2], [3, 1]],
        values=[3, 2, -1, 3],
        dense_shape=[4, 5])
    dense = tf.constant(['a', 'b', 'c', 'd'])
    x, key = tf_utils._get_dense_value_key_inputs(sparse, sparse)
    self.assertAllEqual(self.evaluate(x), sparse.values)
    self.assertAllEqual(self.evaluate(key), sparse.values)

    x, key = tf_utils._get_dense_value_key_inputs(sparse, dense)
    self.assertAllEqual(self.evaluate(x), sparse.values)
    self.assertAllEqual(self.evaluate(key), dense)

    with tf.compat.v1.Graph().as_default():
      sparse1 = tf.compat.v1.sparse_placeholder(tf.int64, shape=[None, None])
      sparse2 = tf.compat.v1.sparse_placeholder(tf.int64, shape=[None, None])
      sparse_value1 = tf.compat.v1.SparseTensorValue(
          indices=[[0, 0], [1, 1], [2, 2], [3, 1]],
          values=[3, 2, -1, 3],
          dense_shape=[4, 5])
      sparse_value2 = tf.compat.v1.SparseTensorValue(
          indices=[[0, 0], [1, 2], [2, 2], [3, 1]],
          values=[3, 2, -1, 3],
          dense_shape=[4, 5])

      with tf.compat.v1.Session() as sess:
        with self.assertRaisesRegexp(exception_cls, error_string):
          sess.run(tf_utils._get_dense_value_key_inputs(sparse1, sparse2),
                   feed_dict={sparse1: sparse_value1, sparse2: sparse_value2})

  @test_case.named_parameters(
      dict(
          testcase_name='dense_tensor',
          key=['b', 'a', 'b'],
          key_vocab=['a', 'b'],
          reductions=([1, 2], [3, 4]),
          x=[5, 6, 7],
          expected_results=([2, 1, 2], [4, 3, 4])
          ),
      dict(
          testcase_name='sparse_tensor',
          key=['b', 'a', 'b'],
          key_vocab=['a', 'b'],
          reductions=([1, 2], [3, 4]),
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [1, 2], [2, 2], [2, 3]],
              values=[3, 2, -1, 3],
              dense_shape=[3, 5]),
          expected_results=([2, 1, 2, 2], [4, 3, 4, 4])
          ),
      dict(
          testcase_name='sparse_tensor_sparse_key',
          key=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [1, 2], [2, 2], [2, 3]],
              values=['b', 'a', 'b', 'b'],
              dense_shape=[3, 5]),
          key_vocab=['a', 'b'],
          reductions=([1, 2], [3, 4]),
          x=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [1, 2], [2, 2], [2, 3]],
              values=[3, 2, -1, 3],
              dense_shape=[3, 5]),
          expected_results=([2, 1, 2, 2], [4, 3, 4, 4])
          ),
  )
  def test_map_per_key_reductions(
      self, key, key_vocab, reductions, x, expected_results):
    with tf.compat.v1.Graph().as_default():
      if isinstance(key, tf.compat.v1.SparseTensorValue):
        key = tf.compat.v1.convert_to_tensor_or_sparse_tensor(key)
      else:
        key = tf.constant(key)
      key_vocab = tf.constant(key_vocab)
      reductions = tuple([tf.constant(t) for t in reductions])
      if isinstance(x, tf.compat.v1.SparseTensorValue):
        x = tf.compat.v1.convert_to_tensor_or_sparse_tensor(x)
      else:
        x = tf.constant(x)
      expected_results = tuple(tf.constant(t) for t in expected_results)
      results = tf_utils.map_per_key_reductions(reductions, key, key_vocab, x)
      with tf.compat.v1.Session() as sess:
        sess.run(tf.compat.v1.tables_initializer())
        output = sess.run(results)
        for result, expected_result in zip(output, expected_results):
          self.assertAllEqual(result, expected_result)

  @test_case.named_parameters(test_case.cross_with_function_handlers([
      dict(
          testcase_name='sparse_tensor',
          feature=tf.compat.v1.SparseTensorValue(
              indices=[[0, 0], [0, 1], [0, 2], [1, 0]],
              values=[1., 2., 3., 4.],
              dense_shape=[2, 5]),
          input_signature=[tf.SparseTensorSpec([None, 5], tf.float32)],
          ascii_protos=[
              'float_list { value: [1.0, 2.0, 3.0] }',
              'float_list { value: [4.0] }',
          ]),
      dict(
          testcase_name='dense_scalar_int',
          feature=[0, 1, 2],
          input_signature=[tf.TensorSpec([None], tf.int64)],
          ascii_protos=[
              'int64_list { value: [0] }',
              'int64_list { value: [1] }',
              'int64_list { value: [2] }',
          ]),
      dict(
          testcase_name='dense_scalar_float',
          feature=[0.5, 1.5, 2.5],
          input_signature=[tf.TensorSpec([None], tf.float32)],
          ascii_protos=[
              'float_list { value: [0.5] }',
              'float_list { value: [1.5] }',
              'float_list { value: [2.5] }',
          ]),
      dict(
          testcase_name='dense_scalar_string',
          feature=['hello', 'world'],
          input_signature=[tf.TensorSpec([None], tf.string)],
          ascii_protos=[
              'bytes_list { value: "hello" }',
              'bytes_list { value: "world" }',
          ]),
      dict(
          testcase_name='dense_vector_int',
          feature=[[0, 1], [2, 3]],
          input_signature=[tf.TensorSpec([None, 2], tf.int64)],
          ascii_protos=[
              'int64_list { value: [0, 1] }',
              'int64_list { value: [2, 3] }',
          ]),
      dict(
          testcase_name='dense_matrix_int',
          feature=[[[0, 1], [2, 3]], [[4, 5], [6, 7]]],
          input_signature=[tf.TensorSpec([None, 2, 2], tf.int64)],
          ascii_protos=[
              'int64_list { value: [0, 1, 2, 3] }',
              'int64_list { value: [4, 5, 6, 7] }',
          ]),
  ]))
  def test_serialize_feature(
      self, feature, input_signature, ascii_protos, function_handler):

    @function_handler(input_signature=input_signature)
    def _serialize_feature(feature):
      return tf_utils._serialize_feature(feature)

    serialized_features = _serialize_feature(feature)

    self.assertEqual(len(ascii_protos), len(serialized_features))
    for ascii_proto, serialized_feature in zip(ascii_protos,
                                               serialized_features):
      feature_proto = tf.train.Feature()
      feature_proto.ParseFromString(serialized_feature)
      self.assertProtoEquals(ascii_proto, feature_proto)

  @test_case.named_parameters(
      dict(
          testcase_name='multiple_features',
          examples={
              'my_value':
                  tf.compat.v1.SparseTensorValue(
                      indices=[[0, 0], [0, 1], [0, 2], [1, 0]],
                      values=[1., 2., 3., 4.],
                      dense_shape=[2, 5]),
              'my_other_value':
                  np.array([1, 2], np.int64),
          },
          ascii_protos=[
              """
               features {
                 feature {
                   key: "my_value"
                   value: { float_list { value: [1, 2, 3] } }
                 }
                 feature {
                   key: "my_other_value"
                    value: { int64_list { value: [1] } }
                 }
               }
               """, """
               features {
                 feature {
                   key: "my_value"
                   value: { float_list { value: [4] } }
                 }
                 feature {
                   key: "my_other_value"
                    value: { int64_list { value: [2] } }
                 }
               }
               """
          ]))
  def test_serialize_example(self, examples, ascii_protos):
    with tf.compat.v1.Graph().as_default():
      serialized_examples_tensor = tf_utils.serialize_example(examples)
      with tf.compat.v1.Session():
        serialized_examples = serialized_examples_tensor.eval()
        example_proto = tf.train.Example()
    self.assertEqual(len(serialized_examples), len(ascii_protos))
    for ascii_proto, serialized_example in zip(ascii_protos,
                                               serialized_examples):
      example_proto.ParseFromString(serialized_example)
      self.assertProtoEquals(ascii_proto, example_proto)

  def test_extend_reduced_batch_with_y_counts(self):
    initial_reduction = tf_utils.ReducedBatchWeightedCounts(
        unique_x=tf.constant(['foo', 'bar']),
        summed_weights_per_x=tf.constant([2.0, 4.0]),
        summed_positive_per_x_and_y=tf.constant([[1.0, 3.0], [1.0, 1.0]]),
        counts_per_x=tf.constant([2, 4], tf.int64))
    y = tf.constant([0, 1, 1, 1, 0, 1, 1], tf.int64)
    extended_batch = tf_utils.extend_reduced_batch_with_y_counts(
        initial_reduction, y)
    self.assertAllEqual(self.evaluate(extended_batch.unique_x),
                        np.array([b'foo', b'bar', b'global_y_count_sentinel']))
    self.assertAllClose(self.evaluate(extended_batch.summed_weights_per_x),
                        np.array([2.0, 4.0, 7.0]))
    self.assertAllClose(
        self.evaluate(extended_batch.summed_positive_per_x_and_y),
        np.array([[1.0, 3.0], [1.0, 1.0], [2.0, 5.0]]))
    self.assertAllClose(self.evaluate(extended_batch.counts_per_x),
                        np.array([2.0, 4.0, 7.0]))


if __name__ == '__main__':
  # TODO(b/133440043): Remove this once this is enabled by default.
  tf.compat.v1.enable_v2_tensorshape()
  test_case.main()
