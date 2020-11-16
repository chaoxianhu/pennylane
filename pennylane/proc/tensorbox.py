# Copyright 2018-2020 Xanadu Quantum Technologies Inc.

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""This module contains the TensorBox abstract base class."""
# pylint: disable=import-outside-toplevel
import abc
import functools
import numbers
import sys
from types import FunctionType

import numpy as np


def wrap_output(func):
    @functools.wraps(func)
    def _wrapper(*args, **kwargs):
        wrap = kwargs.pop("wrap_output", True)

        if wrap:
            cls = vars(sys.modules[func.__module__])[func.__qualname__.split(".")[0]]
            return cls(func(*args, **kwargs))

        return func(*args, **kwargs)

    return _wrapper


class TensorBox(abc.ABC):
    """A container for array-like objects that allows array manipulation to be performed in a
    unified manner for supported tensor/array manipulation frameworks.

    Args:
        tensor (tensor_like): instantiate the ``TensorBox`` container with an array-like object

    .. warning::

        The :class:`TensorBox` class is designed for internal use **only**, to ensure that
        PennyLane templates, cost functions, and optimizers retain differentiability
        across all supported interfaces.

        Consider instead using the function wrappers provided in :mod:`~.tensorbox`.

    By wrapping array-like objects in a ``TensorBox`` class, array manipulations are
    performed by simply chaining method calls. Under the hood, the method call is dispatched
    to the corresponding tensor/array manipulation library based on the wrapped array type, without
    the need to import any external libraries manually. As a result, autodifferentiation is
    preserved where needed.

    **Example**

    While this is an abstract base class, this class may be 'instantiated' directly;
    by overloading ``__new__``, the tensor argument is inspected, and the correct subclass
    is returned:

    >>> x = tf.Variable([0.4, 0.1, 0.5])
    >>> y = TensorBox(x)
    >>> print(y)
    TensorBox: <tf.Variable 'Variable:0' shape=(3,) dtype=float32, numpy=array([0.4, 0.1, 0.5], dtype=float32)>

    The original tensor is available via the :meth:`~.unbox` method or the :attr:`data` attribute:

    >>> y.unbox()
    <tf.Variable 'Variable:0' shape=(3,) dtype=float32, numpy=array([0.4, 0.1, 0.5], dtype=float32)>

    In addition, this class defines various abstract methods that all subclasses
    must define. These methods allow for common manipulations and
    linear algebra transformations without the need for importing.

    >>> y.ones_like()
    tf.Tensor([1. 1. 1.], shape=(3,), dtype=float32)

    Unless specified, the returned tensors are also ``TensorBox`` instances, allowing
    for method chaining:

    >>> y.ones_like().expand_dims(0)
    tf.Tensor([[1. 1. 1.]], shape=(1, 3), dtype=float32)
    """

    _initialized = False

    def __new__(cls, tensor):
        if isinstance(tensor, TensorBox):
            return tensor

        if cls is not TensorBox:
            return super(TensorBox, cls).__new__(cls)

        namespace = tensor.__class__.__module__.split(".")[0]

        if isinstance(tensor, (numbers.Number, list, tuple)) or namespace == "numpy":
            from .numpy_box import NumpyBox

            return NumpyBox.__new__(NumpyBox, tensor)

        if namespace in ("pennylane", "autograd"):
            from .autograd_box import AutogradBox

            return AutogradBox.__new__(AutogradBox, tensor)

        if namespace == "tensorflow":
            from .tf_box import TensorFlowBox

            return TensorFlowBox.__new__(TensorFlowBox, tensor)

        if namespace == "torch":
            from .torch_box import TorchBox

            return TorchBox.__new__(TorchBox, tensor)

        raise ValueError(f"Unknown tensor type {type(tensor)}")

    def __array_ufunc__(self, ufunc, method, *inputs, **kwargs):
        """By defining this special method, NumPy ufuncs can act directly
        on the contained tensor, with broadcasting taken into account. For
        more details, see https://numpy.org/devdocs/user/basics.subclassing.html#array-ufunc-for-ufuncs"""
        outputs = [v.data if isinstance(v, TensorBox) else v for v in kwargs.get("out", ())]

        if outputs:
            # Insert the unwrapped outputs into the keyword
            # args dictionary, to be passed to ndarray.__array_ufunc__
            outputs = tuple(outputs)
            kwargs["out"] = outputs
        else:
            # If the ufunc has no outputs, we simply
            # create a tuple containing None for all potential outputs.
            outputs = (None,) * ufunc.nout

        args = [v.data if isinstance(v, TensorBox) else v for v in inputs]
        res = getattr(ufunc, method)(*args, **kwargs)

        if ufunc.nout == 1:
            res = (res,)

        # construct a list of ufunc outputs to return
        ufunc_output = []
        for result, output in zip(res, outputs):
            if output is not None:
                ufunc_output.append(output)
            else:
                if isinstance(result, np.ndarray):
                    if result.ndim == 0 and result.dtype == np.dtype("bool"):
                        ufunc_output.append(result.item())
                    else:
                        ufunc_output.append(self.__class__(result))
                else:
                    ufunc_output.append(result)

        if len(ufunc_output) == 1:
            # the ufunc has a single output so return a single tensor
            return ufunc_output[0]

        # otherwise we must return a tuple of tensors
        return tuple(ufunc_output)

    def __array_function__(self, func, types, args, kwargs):
        if func not in self.numpy_dispatch_functions:
            return func._implementation(*args, **kwargs)

        dispatch_fn = self.numpy_dispatch_functions[func]

        if callable(dispatch_fn):
            return dispatch_fn(*args, **kwargs)

        dispatch_fn = getattr(self, dispatch_fn)

        if callable(dispatch_fn):
            if isinstance(dispatch_fn, FunctionType):
                # static method
                return dispatch_fn(*args, **kwargs)

            # instance method
            return dispatch_fn(*args[1:], **kwargs)

        # property
        return dispatch_fn

    def __init__(self, tensor):
        if self._initialized:
            return

        self.data = tensor
        self._initialized = True

    def __repr__(self):
        return f"TensorBox: {self.data.__repr__()}"

    def __len__(self):
        return len(self.data)

    def __add__(self, other):
        if isinstance(other, TensorBox):
            other = other.data

        return self.__class__(self.data + other)

    def __sub__(self, other):
        if isinstance(other, TensorBox):
            other = other.data

        return self.__class__(self.data - other)

    def __mul__(self, other):
        if isinstance(other, TensorBox):
            other = other.data

        return self.__class__(self.data * other)

    def __truediv__(self, other):
        if isinstance(other, TensorBox):
            other = other.data

        return self.__class__(self.data / other)

    def __rtruediv__(self, other):
        return self.__class__(other / self.data)

    def __pow__(self, other):
        if isinstance(other, TensorBox):
            other = other.data

        return self.__class__(self.data ** other)

    def __rpow__(self, other):
        return self.__class__(other ** self.data)

    __radd__ = __add__
    __rsub__ = __sub__
    __rmul__ = __mul__

    @staticmethod
    def unbox_list(tensors):
        """Unboxes or unwraps a list of tensor-like objects, converting any :class:`TensorBox`

        objects in the list into raw interface tensors.

        Args:
            tensors (list[tensor_like]): list of arrays, tensors, or :class:`~.TensorBox` objects

        Returns
            list[tensor_like]: the input list with all :class:`TensorBox` objects
            unwrapped

        **Example**

        >>> x = tf.Variable([0.4, 0.1, 0.5])
        >>> y = TensorBox(x)
        >>> z = tf.constant([0.1, 0.2])

        Note that this is a static method, so we must pass the tensor represented by the ``TensorBox``
        if we would like it to be included.

        >>> res = y.unwrap([y, z])
        >>> res
        [<tf.Variable 'Variable:0' shape=(3,) dtype=float32, numpy=array([0.4, 0.1, 0.5], dtype=float32)>,
         <tf.Tensor: shape=(2,), dtype=float32, numpy=array([0.1, 0.2], dtype=float32)>]
        >>> print([type(v) for v in res])
        [<class 'tensorflow.python.ops.resource_variable_ops.ResourceVariable'>,
         <class 'tensorflow.python.framework.ops.EagerTensor'>]
        """
        return [v.data if isinstance(v, TensorBox) else v for v in tensors]

    def unbox(self):
        """Unboxes the ``TensorBox`` container, returning the raw interface tensor."""
        return self.data

    ###############################################################################
    # Abstract methods and properties
    ###############################################################################

    @abc.abstractmethod
    def abs(self):
        """TensorBox: Returns the element-wise absolute value."""

    @abc.abstractmethod
    def angle(self):
        """TensorBox: Returns the elementwise complex angle."""

    @abc.abstractmethod
    def arcsin(self):
        """Returns the element-wise inverse sine of the tensor"""

    @staticmethod
    @abc.abstractmethod
    def astensor(tensor):
        """Converts the input to the native tensor type of the TensorBox.

        Args:
            tensor (tensor_like): array to convert
        """

    @abc.abstractmethod
    def cast(self, dtype):
        """Cast the dtype of the TensorBox.

        Args:
            dtype (np.dtype, str): the NumPy datatype to cast to
                If the boxed tensor is not a NumPy array, the equivalent
                datatype in the target framework is chosen.
        """

    @abc.abstractmethod
    def concatenate(self, values, axis=0):
        """Join a sequence of tensors along an existing axis.

        Args:
            values (Sequence[tensor_like]): sequence of arrays/tensors to concatenate
            axis (int): axis on which to concatenate

        **Example**

        >>> x = tf.Variable([[1, 2], [3, 4]])
        >>> a = tf.constant([[5, 6]])
        >>> y = TensorBox(x)
        >>> y.concatenate([a, y], axis=0)
        <tf.Tensor: shape=(2, 3), dtype=float32, numpy=
        array([[1, 2],
               [3, 4],
               [5, 6]]), dtype=float32)>

        >>> y.concatenate([a, y], axis=1)
        <tf.Tensor: shape=(2, 3), dtype=float32, numpy=
        array([[1, 2, 5],
               [3, 4, 6]]), dtype=float32)>

        >>> y.concatenate([a, y], axis=None)
        <tf.Tensor: shape=(2, 3), dtype=float32, numpy=
        array([1, 2, 3, 4, 5, 6]), dtype=float32)>

        Note that this is a static method, so we must pass the unified tensor itself
        if we would like it to be included.
        """

    @abc.abstractmethod
    def dot(self, other):
        """Returns the matrix or dot product of two tensors.

        * If both tensors are 0-dimensional, elementwise multiplication
          is performed and a 0-dimensional scalar returned.

        * If both tensors are 1-dimensional, the dot product is returned.

        * If the first array is 2-dimensional and the second array 1-dimensional,
          the matrix-vector product is returned.

        * If both tensors are 2-dimensional, the matrix product is returned.

        * Finally, if the the first array is N-dimensional and the second array
          M-dimensional, a sum product over the last dimension of the first array,
          and the second-to-last dimension of the second array is returned.

        Args:
            other (tensor_like): the tensor-like object to right-multiply the TensorBox by
        """

    @abc.abstractmethod
    def expand_dims(self, axis):
        """Expand the shape of the tensor.

        Args:
            axis (int or tuple[int]): the axis or axes where the additional
                dimensions should be inserted
        """

    @property
    @abc.abstractmethod
    def interface(self):
        """str, None: The package that the :class:`.TensorBox` class
        will dispatch to. The returned strings correspond to those used for PennyLane
        :doc:`interfaces </introduction/interfaces>`."""

    @abc.abstractmethod
    def numpy(self):
        """Converts the tensor to a standard, non-differentiable NumPy ndarray, or to a Python scalar if
        the tensor is 0-dimensional.

        Returns:
            array, float, int: NumPy ndarray, or Python scalar if the input is 0-dimensional

        **Example**

        >>> x = tf.Variable([0.4, 0.1, 0.5])
        >>> y = TensorBox(x)
        >>> y.numpy()
        array([0.4, 0.1, 0.5], dtype=float32)
        """

    @abc.abstractmethod
    def ones_like(self):
        """Returns a unified tensor of all ones, with the shape and dtype
        of the unified tensor.

        Returns:
            TensorBox: all ones array

        **Example**

        >>> x = tf.Variable([[0.4, 0.1], [0.1, 0.5]])
        >>> y = TensorBox(x)
        >>> y.ones_like()
        tf.Tensor(
        [[1. 1.]
         [1. 1.]], shape=(2, 2), dtype=float32)
        """

    @property
    @abc.abstractmethod
    def requires_grad(self):
        """bool: Whether the TensorBox is considered trainable.


        Note that the implemetation depends on the contained tensor type, and
        may be context dependent.

        For example, Torch tensors and PennyLane tensors track trainability
        as a property of the tensor itself. TensorFlow, on the other hand,

        only tracks trainability if being watched by a gradient tape.
        """

    @property
    @abc.abstractmethod
    def shape(self):
        """tuple[int]: returns the shape of the tensor as a tuple of integers"""

    @abc.abstractmethod
    def sqrt(self):
        """Returns the square root of the tensor"""

    @staticmethod
    @abc.abstractmethod
    def stack(values, axis=0):
        """Stacks a list of tensors along the specified index.

        Args:
            values (Sequence[tensor_like]): sequence of arrays/tensors to stack
            axis (int): axis on which to stack

        Returns:
            TensorBox: TensorBox containing the stacked array

        **Example**

        >>> x = tf.Variable([0.4, 0.1, 0.5])
        >>> a = tf.constant([1., 2., 3.])
        >>> y = TensorBox(x)
        >>> y.stack([a, y])
        <tf.Tensor: shape=(2, 3), dtype=float32, numpy=
        array([[1. , 2. , 3. ],
               [0.4, 0.1, 0.5]], dtype=float32)>

        Note that this is a static method, so we must pass the unified tensor itself
        if we would like it to be included.
        """

    @abc.abstractmethod
    def sum(self, axis=None, keepdims=False):
        """TensorBox: Returns the sum of the tensor elements across the specified dimensions.

        Args:
            axis (int or tuple[int]): The axis or axes along which to perform the sum.
                If not specified, all elements of the tensor across all dimensions
                will be summed, returning a tensor.
            keepdims (bool): If True, retains all summed dimensions.

        **Example**

        Summing over all dimensions:

        >>> x = tf.Variable([[1., 2.], [3., 4.]])
        >>> y = TensorBox(x)
        >>> y.sum()
        TensorBox: <tf.Tensor: shape=(), dtype=float32, numpy=10.0>

        Summing over specified dimensions:

        >>> x = np.array([[[1, 1], [5, 3]], [[1, 4], [-6, -1]]])
        >>> y = TensorBox(x)
        >>> y.shape
        (2, 2, 2)
        >>> y.sum(axis=(0, 2))
        TensorBox: tensor([7, 1], requires_grad=True)
        >>> y.sum(axis=(0, 2), keepdims=True)
        TensorBox: tensor([[[7],
                            [1]]], requires_grad=True)
        """

    @property
    @abc.abstractmethod
    def T(self):
        """Returns the transpose of the tensor."""

    @abc.abstractmethod
    def take(self, indices, axis=None):
        """Gather elements from a tensor.

        Note that ``tensorbox.take(indices, axis=3)`` is equivalent
        to ``tensor[:, :, :, indices, ...]`` for frameworks that support
        NumPy-like fancy indexing.

        This method is roughly equivalent to ``np.take`` and ``tf.gather``.
        In the case of a 1-dimensional set of indices, it is roughly equivalent
        to ``torch.index_select``, but deviates for multi-dimensional indices.

        Args:
            indices (Sequence[int]): the indices of the values to extract
            axis: The axis over which to select the values. If not provided,
                the tensor is flattened before value extraction.

        **Example**

        >>> x = torch.tensor([[1, 2], [3, 4]])
        >>> y = qml.proc.TensorBox(x)
        >>> y.take([[0, 0], [1, 0]], axis=1)
        TensorBox: tensor([[[1, 1],
                 [2, 1]],

                [[3, 3],
                 [4, 3]]])
        """

    @staticmethod
    @abc.abstractmethod
    def where(condition, x, y):
        """Return a tensor of elements selected from ``x`` if the condition is True,
        ``y`` otherwise."""

    numpy_dispatch_functions = {
        np.angle: "angle",
        np.concatenate: "concatenate",
        np.expand_dims: "expand_dims",
        np.ones_like: "ones_like",
        np.shape: "shape",
        np.stack: "stack",
        np.sum: "sum",
        np.take: "take",
        np.where: "where",
    }
