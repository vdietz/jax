# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import numpy as onp
from absl.testing import absltest
from absl.testing import parameterized

import jax.numpy as np
from jax import test_util as jtu
from jax.abstract_arrays import ShapedArray
from jax import lax
from jax import random
from jax.api import jit, grad, jvp, vjp, trace_to_jaxpr, jacfwd, jacrev, hessian
from jax.api import vmap
from jax.core import unit
from jax.interpreters import partial_eval as pe
from jax.util import partial, curry

from jax.config import config
config.parse_flags_with_absl()


class BatchingTest(jtu.JaxTestCase):

  def testConstantFunction(self):
    ans = vmap(lambda x: 3)(onp.ones(4))
    expected = 3 * onp.ones(4)
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testNestedBatchingMatMat(self):
    matvec = vmap(np.vdot, in_axes=(0, None))
    matmat = vmap(matvec, in_axes=(None, 1), out_axes=1)

    R = onp.random.RandomState(0).randn
    A = R(4, 3)
    B = R(3, 2)

    ans = matmat(A, B)
    expected = onp.dot(A, B)
    self.assertAllClose(ans, expected, check_dtypes=False)

    # this is a crude check that we only call a single dot
    def pv_like(x):
      aval = ShapedArray(onp.shape(x), onp.result_type(x))
      return pe.PartialVal((aval, unit))

    def make_jaxpr(fun, example_args):
      jaxpr, _, _, _ = trace_to_jaxpr(fun, map(pv_like, example_args))
      return jaxpr

    jaxpr = make_jaxpr(matmat, (A, B))
    self.assertEqual(len(jaxpr.eqns), 1)

  def testPerExampleGradients(self):
    def predict(params, inputs):
      for W, b in params:
        outputs = np.dot(W, inputs) + b
        inputs = np.tanh(outputs)
      return outputs

    def loss(params, data):
      inputs, targets = data
      predictions = predict(params, inputs)
      return np.sum((predictions - targets)**2)

    batch_size = 5
    layer_sizes = [3, 2, 4]

    R = onp.random.RandomState(0).randn
    params = [(R(m, n), R(m))
              for m, n in zip(layer_sizes[1:], layer_sizes[:-1])]

    input_vec = R(3)
    target_vec = R(4)
    datum = (input_vec, target_vec)

    input_batch = R(5, 3)
    target_batch = R(5, 4)
    batch = (input_batch, target_batch)

    ans = vmap(partial(grad(loss), params))(batch)

    for ans_pair, param_pair in zip(ans, params):
      dW, db = ans_pair
      W, b = param_pair

      self.assertEqual(dW.shape, (batch_size,) + W.shape)
      self.assertEqual(db.shape, (batch_size,) + b.shape)

  def testJacobians(self):
    def jacbwd(f, x):
      y, pullback = vjp(f, x)
      std_basis = onp.eye(onp.size(y)).reshape((-1,) + onp.shape(y))
      jac_flat, = vmap(pullback, out_axes=onp.ndim(y))(std_basis)
      return jac_flat.reshape(onp.shape(y) + onp.shape(x))

    def jacfwd(f, x):
      pushfwd = lambda v: jvp(f, (x,), (v,))
      std_basis = onp.eye(onp.size(x)).reshape((-1,) + onp.shape(x))
      y, jac_flat = vmap(pushfwd, out_axes=(None, 0))(std_basis)
      return jac_flat.reshape(onp.shape(y) + onp.shape(x))

    R = onp.random.RandomState(0).randn

    A = R(4, 3)
    b = R(4)
    f = lambda x: np.tanh(np.dot(A, x) + b)

    x = R(3)
    self.assertAllClose(jacfwd(f, x), jacbwd(f, x), check_dtypes=False)

  def testBatchOfCompile(self):
    side = []

    @jit
    def f(x):
      side.append(None)
      return x + x

    g = jit(vmap(f))
    self.assertAllClose(g(onp.ones(2)), 2 * onp.ones(2), check_dtypes=False)
    self.assertEqual(len(side), 1)
    self.assertAllClose(g(2 * onp.ones(2)), 4 * onp.ones(2),
                        check_dtypes=False)
    self.assertEqual(len(side), 1)

  def testSliceLax(self):
    fun = lambda x: lax.slice(x, (2,), (4,))
    R = onp.random.RandomState(0).randn
    x = R(5, 10)

    ans = vmap(fun)(x)
    expected_ans = x[:, 2:4]
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

  def testSliceNumpy(self):
    fun = lambda x: x[:, 2]
    R = onp.random.RandomState(0).randn
    x = R(10, 5, 3, 7)

    ans = vmap(fun)(x)
    expected_ans = x[:, :, 2]
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

  def testRevLax(self):
    fun = lambda x: lax.rev(x, [0])
    R = onp.random.RandomState(0).randn
    x = R(2, 3)

    ans = vmap(fun)(x)
    expected_ans = x[:, ::-1]
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

    ans = vmap(fun, (1,), 1)(x)
    expected_ans = x[::-1, :]
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

  def testRevNumpy(self):
    fun = lambda x: x[:, ::-1]
    R = onp.random.RandomState(0).randn
    x = R(3, 2, 4)

    ans = vmap(fun)(x)
    expected_ans = x[:, :, ::-1]
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

    ans = vmap(fun, (1,), 1)(x)
    expected_ans = x[:, :, ::-1]
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

    ans = vmap(fun, (2,), 2)(x)
    expected_ans = x[:, ::-1, :]
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

  def testNpMaximum(self):
    fun = lambda x: np.maximum(x, 0.0)
    R = onp.random.RandomState(0).randn
    x = R(10, 5, 3, 7)

    ans = vmap(fun)(x)
    expected_ans = onp.maximum(x, 0.0)
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

  def testNpGtrThan(self):
    R = onp.random.RandomState(0).randn
    x = R(10, 5, 3, 7)

    ans = vmap(lambda x: x > 1.0)(x)
    expected_ans = x > 1.0
    self.assertAllClose(ans, expected_ans, check_dtypes=True)

  def testNpMaximumPerExampleGrad(self):
    R = onp.random.RandomState(0).randn
    x = R(10, 5)
    W = R(5, 5)

    fun = lambda W, x: np.sum(np.maximum(np.dot(x, W), 0.0) ** 2)

    ans = vmap(partial(grad(fun), W))(x)

    W_t = np.transpose(W)
    for i in range(10):
      x_ex = x[i:i + 1]

      expected_ans = 2.0 * np.dot(
          np.maximum(np.dot(W_t, np.transpose(x_ex)), 0.0), x_ex)
      expected_ans = np.transpose(expected_ans)

      self.assertAllClose(ans[i], expected_ans, check_dtypes=False)

  def testDotGeneral(self):
    R = onp.random.RandomState(0).randn

    x = R(10, 3, 4, 5)
    y = R(10, 3, 5, 6)
    fun = lambda x, y: lax.dot_general(x, y, [((2,), (1,)), ((0,), (0,))])
    ans = vmap(fun)(x, y)
    expected = lax.dot_general(x, y, [((3,), (2,)), ((0, 1), (0, 1))])
    self.assertAllClose(ans, expected, check_dtypes=True)

    x = R(3, 4, 10, 5)
    y = R(3, 10, 5, 6)
    fun = lambda x, y: lax.dot_general(x, y, [((2,), (1,)), ((0,), (0,))])
    ans = vmap(fun, in_axes=(2, 1))(x, y)
    fun = lambda x, y: lax.dot_general(x, y, [((2,), (1,)), ((0,), (0,))])
    expected = onp.stack([fun(x[..., i, :], y[:, i, ...]) for i in range(10)])
    self.assertAllClose(ans, expected, check_dtypes=True)

    x = R(3, 4, 5, 10)
    y = R(3, 5, 6)
    fun = lambda x, y: lax.dot_general(x, y, [((2,), (1,)), ((0,), (0,))])
    ans = vmap(fun, in_axes=(3, None))(x, y)
    fun = lambda x, y: lax.dot_general(x, y, [((2,), (1,)), ((0,), (0,))])
    expected = onp.stack([fun(x[..., i], y) for i in range(10)])
    self.assertAllClose(ans, expected, check_dtypes=True)

    x = R(3, 4, 5)
    y = R(3, 5, 10, 6)
    fun = lambda x, y: lax.dot_general(x, y, [((2,), (1,)), ((0,), (0,))])
    ans = vmap(fun, in_axes=(None, 2))(x, y)
    fun = lambda x, y: lax.dot_general(x, y, [((2,), (1,)), ((0,), (0,))])
    expected = onp.stack([fun(x, y[..., i, :]) for i in range(10)])
    self.assertAllClose(ans, expected, check_dtypes=True)

  def testDot(self):
    # these tests are based on @shoyer's notebook studying gufuncs

    def vecvec(a, b):
      dot = np.dot
      for ndim in range(1, max(a.ndim, b.ndim)):
        a_ax = 0 if a.ndim > ndim else None
        b_ax = 0 if b.ndim > ndim else None
        dot = vmap(dot, in_axes=(a_ax, b_ax))
      return dot(a, b)

    assert vecvec(np.zeros((3,)), np.zeros((3,))).shape == ()
    assert vecvec(np.zeros((2, 3)), np.zeros((3,))).shape == (2,)
    # TODO(mattjj): this fails due to an xla error in dot_general
    # assert vecvec(np.zeros((4, 2, 3)), np.zeros((3,))).shape == (4, 2)

  def testPad(self):
    R = onp.random.RandomState(0).randn

    fun = lambda x: lax.pad(x, onp.float32(0), [(1, 2, 1)])
    x = R(5, 10).astype(onp.float32)
    ans = vmap(fun)(x)
    expected_ans = np.stack(list(map(fun, x)))
    self.assertAllClose(ans, expected_ans, check_dtypes=False)


    fun = lambda x: lax.pad(x, onp.float32(0), [(1, 2, 1), (0, 1, 0)])
    x = R(5, 10, 3).astype(onp.float32)
    ans = vmap(fun)(x)
    expected_ans = np.stack(list(map(fun, x)))
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

  def testConcatenate(self):
    R = lambda *shape: onp.random.RandomState(0).randn(*shape).astype(onp.float32)

    fun = lambda *args: lax.concatenate(args, dimension=0)
    x, y, z = R(10, 2, 3), R(1, 10, 3), R(4, 3)
    ans = vmap(fun, in_axes=(0, 1, None))(x, y, z)
    expected_ans = onp.concatenate([x, onp.swapaxes(y, 0, 1),
                                    onp.broadcast_to(z, (10, 4, 3))], 1)
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

    fun = lambda *args: lax.concatenate(args, dimension=1)
    x, y, z = R(10, 2, 1), R(2, 3), R(2, 4, 10)
    ans = vmap(fun, in_axes=(0, None, 2))(x, y, z)
    expected_ans = onp.concatenate([x, onp.broadcast_to(y, (10, 2, 3)),
                                    onp.moveaxis(z, 2, 0)], 2)
    self.assertAllClose(ans, expected_ans, check_dtypes=False)

  def testJacobianIssue54(self):
    # test modeling the code in https://github.com/google/jax/issues/54

    def func(xs):
      return np.array([x for x in xs])

    xs = np.ones((5, 1))
    jacrev(func)(xs)  # don't crash
    jacfwd(func)(xs)  # don't crash

  def testAny(self):
    # test modeling the code in https://github.com/google/jax/issues/108

    ans = vmap(np.any)(np.array([[True, False], [False, False]]))
    expected = np.array([True, False])
    self.assertAllClose(ans, expected, check_dtypes=True)

  @jtu.skip_on_devices("tpu")
  def testHessian(self):
    # test based on code from sindhwani@google
    def fun(x, t):
      return np.sum(np.power(np.maximum(x, 0.0), 2)) + t

    x = onp.array([-1., -0.5, 0., 0.5, 1.0])

    ans = hessian(lambda x: fun(x, 0.0))(x)
    expected = onp.array([[0., 0., 0., 0., 0.],
                          [0., 0., 0., 0., 0.],
                          [0., 0.,0.5, 0., 0.],
                          [0., 0., 0., 2., 0.],
                          [0., 0., 0., 0., 2.]])
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testDynamicSlice(self):
    # test dynamic_slice via numpy indexing syntax
    x = onp.arange(30).reshape((10, 3))

    ans = vmap(lambda x, i: x[i], in_axes=(0, None))(x, 1)
    expected = x[:, 1]
    self.assertAllClose(ans, expected, check_dtypes=False)


    idx = onp.array([0, 1, 2, 1, 0] * 2)
    ans = vmap(lambda x, i: x[i], in_axes=(0, 0))(x, idx)
    expected = x[onp.arange(10), idx]
    self.assertAllClose(ans, expected, check_dtypes=False)

    x = onp.arange(3)
    idx = onp.array([0, 1, 2, 1, 0] * 2)
    ans = vmap(lambda x, i: x[i], in_axes=(None, 0))(x, idx)
    expected = x[idx]
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testRandom(self):
    seeds = vmap(random.PRNGKey)(onp.arange(10))
    ans = vmap(partial(random.normal, shape=(3, 2)))(seeds)
    expected = onp.stack([random.normal(random.PRNGKey(seed), (3, 2))
                          for seed in onp.arange(10)])
    self.assertAllClose(ans, expected, check_dtypes=False)
    assert len(onp.unique(ans)) == 10 * 3 * 2

  def testSortKeyVal(self):
    k = onp.arange(12)[::-1].reshape(3, 4)
    v = onp.random.RandomState(0).permutation(12).reshape(3, 4)

    sk, sv = vmap(partial(lax.sort_key_val, dimension=0), (0, 0))(k, v)
    self.assertAllClose(sk, k[:, ::-1], check_dtypes=True)
    self.assertAllClose(sv, v[:, ::-1], check_dtypes=True)

    sk, sv = vmap(partial(lax.sort_key_val, dimension=0), (1, 1), 1)(k, v)
    self.assertAllClose(sk, k[::-1, :], check_dtypes=True)
    self.assertAllClose(sv, v[::-1, :], check_dtypes=True)

    sk, sv = vmap(partial(lax.sort_key_val, dimension=0), (0, 1))(k, v.T)
    self.assertAllClose(sk, k[:, ::-1], check_dtypes=True)
    self.assertAllClose(sv, v[:, ::-1], check_dtypes=True)

    sk, sv = vmap(partial(lax.sort_key_val, dimension=0), (1, 0))(k.T, v)
    self.assertAllClose(sk, k[:, ::-1], check_dtypes=True)
    self.assertAllClose(sv, v[:, ::-1], check_dtypes=True)

    sk, sv = vmap(partial(lax.sort_key_val, dimension=0), (None, 0))(k[0], v)
    self.assertAllClose(sk, onp.broadcast_to(k[0, ::-1], (3, 4)),
                        check_dtypes=True)
    self.assertAllClose(sv, v[:, ::-1], check_dtypes=True)

    sk, sv = vmap(partial(lax.sort_key_val, dimension=0), (1, None))(k.T, v[0])
    self.assertAllClose(sk, k[:, ::-1], check_dtypes=True)
    self.assertAllClose(sv, onp.broadcast_to(v[0, ::-1], (3, 4)),
                        check_dtypes=True)

  def testConvGeneralDilated(self):
    W = np.array(onp.random.randn(3, 3, 1, 5), dtype=onp.float32)
    X = np.array(onp.random.randn(10, 5, 5, 1), dtype=onp.float32)

    def f(params, x):
      one = (1, 1)
      dimension_numbers = ('NHWC', 'HWIO', 'NHWC')
      y = lax.conv_general_dilated(
          x, params, one, 'SAME', one, one, dimension_numbers)
      return y
    grad_loss = grad(lambda params, x: np.mean(f(params, x) ** 2))

    # Test forward prop.
    per_example = vmap(partial(f, W))(np.reshape(X, (10, 1, 5, 5, 1)))
    per_example = np.reshape(per_example, (10, 5, 5, 5))
    per_example_direct = f(W, X)
    self.assertAllClose(per_example, per_example_direct, check_dtypes=True)

    # Test gradients.
    per_example = vmap(partial(grad_loss, W))(np.reshape(X, (10, 1, 5, 5, 1)))
    per_example_direct = []
    for i in range(10):
      g = grad_loss(W, np.reshape(X[i], (1, 5, 5, 1)))
      per_example_direct += [
          np.reshape(g, (1,) + g.shape)]
    per_example_direct = np.concatenate(per_example_direct, axis=0)
    self.assertAllClose(per_example, per_example_direct, check_dtypes=True)

  def testMaxPool(self):
    W = np.array(onp.random.randn(3, 3, 1, 5), dtype=onp.float32)
    X = np.array(onp.random.randn(10, 5, 5, 1), dtype=onp.float32)

    def f(params, x):
      one = (1, 1)
      dimension_numbers = ('NHWC', 'HWIO', 'NHWC')
      y = lax.conv_general_dilated(
          x, params, one, 'SAME', one, one, dimension_numbers)
      y = lax.reduce_window(
          y, -np.inf, lax.max, (1, 2, 2, 1), (1, 1, 1, 1), 'SAME')
      return y
    grad_loss = grad(lambda params, x: np.mean(f(params, x) ** 2))

    # Test forward prop.
    per_example = vmap(partial(f, W))(np.reshape(X, (10, 1, 5, 5, 1)))
    per_example = np.reshape(per_example, (10, 5, 5, 5))
    per_example_direct = f(W, X)
    self.assertAllClose(per_example, per_example_direct, check_dtypes=True)

    # Test gradients.
    per_example = vmap(partial(grad_loss, W))(np.reshape(X, (10, 1, 5, 5, 1)))
    per_example_direct = []
    for i in range(10):
      g = grad_loss(W, np.reshape(X[i], (1, 5, 5, 1)))
      per_example_direct += [
          np.reshape(g, (1,) + g.shape)]
    per_example_direct = np.concatenate(per_example_direct, axis=0)
    self.assertAllClose(per_example, per_example_direct, check_dtypes=True)

  def testSumPool(self):
    W = np.array(onp.random.randn(3, 3, 1, 5), dtype=onp.float32)
    X = np.array(onp.random.randn(10, 5, 5, 1), dtype=onp.float32)

    def f(params, x):
      one = (1, 1)
      dimension_numbers = ('NHWC', 'HWIO', 'NHWC')
      y = lax.conv_general_dilated(
          x, params, one, 'SAME', one, one, dimension_numbers)
      y = lax.reduce_window(
          y, 0.0, lax.add, (1, 2, 2, 1), (1, 1, 1, 1), 'SAME')
      return y
    grad_loss = grad(lambda params, x: np.mean(f(params, x) ** 2))

    # Test forward prop.
    per_example = vmap(partial(f, W))(np.reshape(X, (10, 1, 5, 5, 1)))
    per_example = np.reshape(per_example, (10, 5, 5, 5))
    per_example_direct = f(W, X)
    self.assertAllClose(per_example, per_example_direct, check_dtypes=True)

    # Test gradients.
    per_example = vmap(partial(grad_loss, W))(np.reshape(X, (10, 1, 5, 5, 1)))
    per_example_direct = []
    for i in range(10):
      g = grad_loss(W, np.reshape(X[i], (1, 5, 5, 1)))
      per_example_direct += [
          np.reshape(g, (1,) + g.shape)]
    per_example_direct = np.concatenate(per_example_direct, axis=0)
    self.assertAllClose(per_example, per_example_direct, check_dtypes=True)

if __name__ == '__main__':
  absltest.main()
