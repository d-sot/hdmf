import copy as _copy
from abc import ABCMeta
import collections
import h5py
import numpy as np

__macros = {
    'array_data': [np.ndarray, list, tuple, h5py.Dataset],
    'scalar_data': [str, int, float],
}


def docval_macro(macro):
    """Class decorator to add the class to a list of types associated with the key macro in the __macros dict
    """
    def _dec(cls):
        if macro not in __macros:
            __macros[macro] = list()
        __macros[macro].append(cls)
        return cls
    return _dec


def __type_okay(value, argtype, allow_none=False):
    """Check a value against a type

       The difference between this function and :py:func:`isinstance` is that
       it allows specifying a type as a string. Furthermore, strings allow for specifying more general
       types, such as a simple numeric type (i.e. ``argtype``="num").

       Args:
           value (any): the value to check
           argtype (type, str): the type to check for
           allow_none (bool): whether or not to allow None as a valid value


       Returns:
           bool: True if value is a valid instance of argtype
    """
    if value is None:
        return allow_none
    if isinstance(argtype, str):
        if argtype in __macros:
            return __type_okay(value, __macros[argtype], allow_none=allow_none)
        elif argtype == 'int':
            return __is_int(value)
        elif argtype == 'float':
            return __is_float(value)
        elif argtype == 'bool':
            return __is_bool(value)
        return argtype in [cls.__name__ for cls in value.__class__.__mro__]
    elif isinstance(argtype, type):
        if argtype is int:
            return __is_int(value)
        elif argtype is float:
            return __is_float(value)
        elif argtype is bool:
            return __is_bool(value)
        return isinstance(value, argtype)
    elif isinstance(argtype, tuple) or isinstance(argtype, list):
        return any(__type_okay(value, i) for i in argtype)
    else:    # argtype is None
        return True


def __shape_okay_multi(value, argshape):
    if type(argshape[0]) in (tuple, list):  # if multiple shapes are present
        return any(__shape_okay(value, a) for a in argshape)
    else:
        return __shape_okay(value, argshape)


def __shape_okay(value, argshape):
    valshape = get_data_shape(value)
    if not len(valshape) == len(argshape):
        return False
    for a, b in zip(valshape, argshape):
        if b not in (a, None):
            return False
    return True


def __is_int(value):
    return any(isinstance(value, i) for i in (int, np.int8, np.int16, np.int32, np.int64))


def __is_float(value):
    SUPPORTED_FLOAT_TYPES = [float, np.float16, np.float32, np.float64]
    if hasattr(np, "float128"):
        SUPPORTED_FLOAT_TYPES.append(np.float128)
    if hasattr(np, "longdouble"):
        # on windows python<=3.5, h5py floats resolve float64s as either np.float64 or np.longdouble
        # non-deterministically. a future version of h5py will fix this. see #112
        SUPPORTED_FLOAT_TYPES.append(np.longdouble)
    return any(isinstance(value, i) for i in SUPPORTED_FLOAT_TYPES)


def __is_bool(value):
    return isinstance(value, bool) or isinstance(value, np.bool_)


def __format_type(argtype):
    if isinstance(argtype, str):
        return argtype
    elif isinstance(argtype, type):
        return argtype.__name__
    elif isinstance(argtype, tuple) or isinstance(argtype, list):
        types = [__format_type(i) for i in argtype]
        if len(types) > 1:
            return "%s or %s" % (", ".join(types[:-1]), types[-1])
        else:
            return types[0]
    elif argtype is None:
        return "any type"
    else:
        raise ValueError("argtype must be a type, str, list, or tuple")


def __parse_args(validator, args, kwargs, enforce_type=True, enforce_shape=True, allow_extra=False):   # noqa: C901
    """
    Internal helper function used by the docval decorator to parse and validate function arguments

    :param validator: List of dicts from docval with the description of the arguments
    :param args: List of the values of positional arguments supplied by the caller
    :param kwargs: Dict keyword arguments supplied by the caller where keys are the argument name and
                   values are the argument value.
    :param enforce_type: Boolean indicating whether the type of arguments should be enforced
    :param enforce_shape: Boolean indicating whether the dimensions of array arguments
                          should be enforced if possible.
    :param allow_extra: Boolean indicating whether extra keyword arguments are allowed (if False and extra keyword
                        arguments are specified, then an error is raised).

    :return: Dict with:
        * 'args' : Dict all arguments where keys are the names and values are the values of the arguments.
        * 'errors' : List of string with error messages
    """
    ret = dict()
    type_errors = list()
    value_errors = list()
    argsi = 0
    extras = dict()  # has to be initialized to empty here, to avoid spurious errors reported upon early raises

    try:
        # check for duplicates in docval
        names = [x['name'] for x in validator]
        duplicated = [item for item, count in collections.Counter(names).items()
                      if count > 1]
        if duplicated:
            raise ValueError(
                'The following names are duplicated: {}'.format(duplicated))

        if allow_extra:  # extra keyword arguments are allowed so do not consider them when checking number of args
            if len(args) > len(validator):
                raise TypeError(
                    'Expected at most %d arguments %r, got %d positional' % (len(validator), names, len(args))
                )
        else:  # allow for keyword args
            if len(args) + len(kwargs) > len(validator):
                raise TypeError(
                    'Expected at most %d arguments %r, got %d: %d positional and %d keyword %s'
                    % (len(validator), names, len(args) + len(kwargs), len(args), len(kwargs), sorted(kwargs))
                )

        # iterate through the docval specification and find a matching value in args / kwargs
        it = iter(validator)
        arg = next(it)

        # catch unsupported keys
        allowable_terms = ('name', 'doc', 'type', 'shape', 'default', 'help')
        unsupported_terms = set(arg.keys()) - set(allowable_terms)
        if unsupported_terms:
            raise ValueError('docval for {}: {} are not supported by docval'.format(arg['name'],
                                                                                    sorted(unsupported_terms)))
        # process positional arguments of the docval specification (no default value)
        extras = dict(kwargs)
        while True:
            if 'default' in arg:
                break
            argname = arg['name']
            argval_set = False
            if argname in kwargs:
                # if this positional arg is specified by a keyword arg and there are remaining positional args that
                # have not yet been matched, then it is undetermined what those positional args match to. thus, raise
                # an error
                if argsi < len(args):
                    type_errors.append("got multiple values for argument '%s'" % argname)
                argval = kwargs.get(argname)
                extras.pop(argname, None)
                argval_set = True
            elif argsi < len(args):
                argval = args[argsi]
                argval_set = True

            if not argval_set:
                type_errors.append("missing argument '%s'" % argname)
            else:
                if enforce_type:
                    if not __type_okay(argval, arg['type']):
                        if argval is None:
                            fmt_val = (argname, __format_type(arg['type']))
                            type_errors.append("None is not allowed for '%s' (expected '%s', not None)" % fmt_val)
                        else:
                            fmt_val = (argname, type(argval).__name__, __format_type(arg['type']))
                            type_errors.append("incorrect type for '%s' (got '%s', expected '%s')" % fmt_val)
                if enforce_shape and 'shape' in arg:
                    valshape = get_data_shape(argval)
                    while valshape is None:
                        if argval is None:
                            break
                        if not hasattr(argval, argname):
                            fmt_val = (argval, argname, arg['shape'])
                            value_errors.append("cannot check shape of object '%s' for argument '%s' "
                                                "(expected shape '%s')" % fmt_val)
                            break
                        # unpack, e.g. if TimeSeries is passed for arg 'data', then TimeSeries.data is checked
                        argval = getattr(argval, argname)
                        valshape = get_data_shape(argval)
                    if valshape is not None and not __shape_okay_multi(argval, arg['shape']):
                        fmt_val = (argname, valshape, arg['shape'])
                        value_errors.append("incorrect shape for '%s' (got '%s', expected '%s')" % fmt_val)
                ret[argname] = argval
            argsi += 1
            arg = next(it)

        # process arguments of the docval specification with a default value
        while True:
            argname = arg['name']
            if argname in kwargs:
                ret[argname] = kwargs.get(argname)
                extras.pop(argname, None)
            elif len(args) > argsi:
                ret[argname] = args[argsi]
                argsi += 1
            else:
                ret[argname] = _copy.deepcopy(arg['default'])
            argval = ret[argname]
            if enforce_type:
                if not __type_okay(argval, arg['type'], arg['default'] is None):
                    if argval is None and arg['default'] is None:
                        fmt_val = (argname, __format_type(arg['type']))
                        type_errors.append("None is not allowed for '%s' (expected '%s', not None)" % fmt_val)
                    else:
                        fmt_val = (argname, type(argval).__name__, __format_type(arg['type']))
                        type_errors.append("incorrect type for '%s' (got '%s', expected '%s')" % fmt_val)
            if enforce_shape and 'shape' in arg and argval is not None:
                valshape = get_data_shape(argval)
                while valshape is None:
                    if argval is None:
                        break
                    if not hasattr(argval, argname):
                        fmt_val = (argval, argname, arg['shape'])
                        value_errors.append("cannot check shape of object '%s' for argument '%s' (expected shape '%s')"
                                            % fmt_val)
                        break
                    # unpack, e.g. if TimeSeries is passed for arg 'data', then TimeSeries.data is checked
                    argval = getattr(argval, argname)
                    valshape = get_data_shape(argval)
                if valshape is not None and not __shape_okay_multi(argval, arg['shape']):
                    fmt_val = (argname, valshape, arg['shape'])
                    value_errors.append("incorrect shape for '%s' (got '%s', expected '%s')" % fmt_val)
            arg = next(it)
    except StopIteration:
        pass
    except TypeError as e:
        type_errors.append(str(e))
    except ValueError as e:
        value_errors.append(str(e))

    if not allow_extra:
        for key in extras.keys():
            type_errors.append("unrecognized argument: '%s'" % key)
    else:
        # TODO: Extras get stripped out if function arguments are composed with fmt_docval_args.
        # allow_extra needs to be tracked on a function so that fmt_docval_args doesn't strip them out
        for key in extras.keys():
            ret[key] = extras[key]
    return {'args': ret, 'type_errors': type_errors, 'value_errors': value_errors}


docval_idx_name = '__dv_idx__'
docval_attr_name = '__docval__'
__docval_args_loc = 'args'


def get_docval(func, *args):
    '''Get a copy of docval arguments for a function.
    If args are supplied, return only docval arguments with value for 'name' key equal to the args
    '''
    func_docval = getattr(func, docval_attr_name, None)
    if func_docval:
        if args:
            docval_idx = getattr(func, docval_idx_name, None)
            try:
                return tuple(docval_idx[name] for name in args)
            except KeyError as ke:
                raise ValueError('Function %s does not have docval argument %s' % (func.__name__, str(ke)))
        return tuple(func_docval[__docval_args_loc])
    else:
        if args:
            raise ValueError('Function %s has no docval arguments' % func.__name__)
        return tuple()

# def docval_wrap(func, is_method=True):
#    if is_method:
#        @docval(*get_docval(func))
#        def method(self, **kwargs):
#
#            return call_docval_args(func, kwargs)
#        return method
#    else:
#        @docval(*get_docval(func))
#        def static_method(**kwargs):
#            return call_docval_args(func, kwargs)
#        return method


def fmt_docval_args(func, kwargs):
    ''' Separate positional and keyword arguments

    Useful for methods that wrap other methods
    '''
    func_docval = getattr(func, docval_attr_name, None)
    ret_args = list()
    ret_kwargs = dict()
    kwargs_copy = _copy.copy(kwargs)
    if func_docval:
        for arg in func_docval[__docval_args_loc]:
            val = kwargs_copy.pop(arg['name'], None)
            if 'default' in arg:
                if val is not None:
                    ret_kwargs[arg['name']] = val
            else:
                ret_args.append(val)
        if func_docval['allow_extra']:
            ret_kwargs.update(kwargs_copy)
    else:
        raise ValueError('no docval found on %s' % str(func))
    return ret_args, ret_kwargs


def call_docval_func(func, kwargs):
    fargs, fkwargs = fmt_docval_args(func, kwargs)
    return func(*fargs, **fkwargs)


def __resolve_type(t):
    if t is None:
        return t
    if isinstance(t, str):
        if t in __macros:
            return tuple(__macros[t])
        else:
            return t
    elif isinstance(t, type):
        return t
    elif isinstance(t, (list, tuple)):
        ret = list()
        for i in t:
            resolved = __resolve_type(i)
            if isinstance(resolved, tuple):
                ret.extend(resolved)
            else:
                ret.append(resolved)
        return tuple(ret)
    else:
        msg = "argtype must be a type, a str, a list, a tuple, or None - got %s" % type(t)
        raise ValueError(msg)


def docval(*validator, **options):
    '''A decorator for documenting and enforcing type for instance method arguments.

    This decorator takes a list of dictionaries that specify the method parameters. These
    dictionaries are used for enforcing type and building a Sphinx docstring.

    The first arguments are dictionaries that specify the positional
    arguments and keyword arguments of the decorated function. These dictionaries
    must contain the following keys: ``'name'``, ``'type'``, and ``'doc'``. This will define a
    positional argument. To define a keyword argument, specify a default value
    using the key ``'default'``. To validate the dimensions of an input array
    add the optional ``'shape'`` parameter.

    The decorated method must take ``self`` and ``**kwargs`` as arguments.

    When using this decorator, the functions :py:func:`getargs` and
    :py:func:`popargs` can be used for easily extracting arguments from
    kwargs.

    The following code example demonstrates the use of this decorator:

    .. code-block:: python

       @docval({'name': 'arg1':,   'type': str,           'doc': 'this is the first positional argument'},
               {'name': 'arg2':,   'type': int,           'doc': 'this is the second positional argument'},
               {'name': 'kwarg1':, 'type': (list, tuple), 'doc': 'this is a keyword argument', 'default': list()},
               returns='foo object', rtype='Foo'))
       def foo(self, **kwargs):
           arg1, arg2, kwarg1 = getargs('arg1', 'arg2', 'kwarg1', **kwargs)
           ...

    :param enforce_type: Enforce types of input parameters (Default=True)
    :param returns: String describing the return values
    :param rtype: String describing the data type of the return values
    :param is_method: True if this is decorating an instance or class method, False otherwise (Default=True)
    :param enforce_shape: Enforce the dimensions of input arrays (Default=True)
    :param validator: :py:func:`dict` objects specifying the method parameters
    :param options: additional options for documenting and validating method parameters
    '''
    enforce_type = options.pop('enforce_type', True)
    enforce_shape = options.pop('enforce_shape', True)
    returns = options.pop('returns', None)
    rtype = options.pop('rtype', None)
    is_method = options.pop('is_method', True)
    allow_extra = options.pop('allow_extra', False)

    def dec(func):
        _docval = _copy.copy(options)
        _docval['allow_extra'] = allow_extra
        func.__name__ = _docval.get('func_name', func.__name__)
        func.__doc__ = _docval.get('doc', func.__doc__)
        pos = list()
        kw = list()
        for a in validator:
            try:
                a['type'] = __resolve_type(a['type'])
            except Exception as e:
                msg = "error parsing '%s' argument' : %s" % (a['name'], e.args[0])
                raise Exception(msg)
            if 'default' in a:
                kw.append(a)
            else:
                pos.append(a)
        loc_val = pos+kw
        _docval[__docval_args_loc] = loc_val

        def func_call(*args, **kwargs):
            parsed = __parse_args(
                        loc_val,
                        args[1:] if is_method else args,
                        kwargs,
                        enforce_type=enforce_type,
                        enforce_shape=enforce_shape,
                        allow_extra=allow_extra)

            for error_type, ExceptionType in (('type_errors', TypeError),
                                              ('value_errors', ValueError)):
                parse_err = parsed.get(error_type)
                if parse_err:
                    msg = '%s: %s' % (func.__qualname__, ', '.join(parse_err))
                    raise ExceptionType(msg)

            if is_method:
                return func(args[0], **parsed['args'])
            else:
                return func(**parsed['args'])

        _rtype = rtype
        if isinstance(rtype, type):
            _rtype = rtype.__name__
        docstring = __googledoc(func, _docval[__docval_args_loc], returns=returns, rtype=_rtype)
        docval_idx = {a['name']: a for a in _docval[__docval_args_loc]}  # cache a name-indexed dictionary of args
        setattr(func_call, '__doc__', docstring)
        setattr(func_call, '__name__', func.__name__)
        setattr(func_call, docval_attr_name, _docval)
        setattr(func_call, docval_idx_name, docval_idx)
        setattr(func_call, '__module__', func.__module__)
        return func_call
    return dec


def __sig_arg(argval):
    if 'default' in argval:
        default = argval['default']
        if isinstance(default, str):
            default = "'%s'" % default
        else:
            default = str(default)
        return "%s=%s" % (argval['name'], default)
    else:
        return argval['name']


def __builddoc(func, validator, docstring_fmt, arg_fmt, ret_fmt=None, returns=None, rtype=None):
    '''Generate a Spinxy docstring'''
    def to_str(argtype):
        if isinstance(argtype, type):
            module = argtype.__module__
            name = argtype.__name__

            if module.startswith("h5py") or module.startswith("pandas") or module.startswith("builtins"):
                return ":py:class:`~{name}`".format(name=name)
            else:
                return ":py:class:`~{module}.{name}`".format(name=name, module=module)
        return argtype

    def __sphinx_arg(arg):
        fmt = dict()
        fmt['name'] = arg.get('name')
        fmt['doc'] = arg.get('doc')
        if isinstance(arg['type'], tuple) or isinstance(arg['type'], list):
            fmt['type'] = " or ".join(map(to_str, arg['type']))
        else:
            fmt['type'] = to_str(arg['type'])
        return arg_fmt.format(**fmt)

    sig = "%s(%s)\n\n" % (func.__name__, ", ".join(map(__sig_arg, validator)))
    desc = func.__doc__.strip() if func.__doc__ is not None else ""
    sig += docstring_fmt.format(description=desc, args="\n".join(map(__sphinx_arg, validator)))

    if not (ret_fmt is None or returns is None or rtype is None):
        sig += ret_fmt.format(returns=returns, rtype=rtype)
    return sig


def __sphinxdoc(func, validator, returns=None, rtype=None):
    arg_fmt = (":param {name}: {doc}\n"
               ":type  {name}: {type}")
    docstring_fmt = ("{description}\n\n"
                     "{args}\n")
    ret_fmt = (":returns: {returns}\n"
               ":rtype: {rtype}")
    return __builddoc(func, validator, docstring_fmt, arg_fmt, ret_fmt=ret_fmt, returns=returns, rtype=rtype)


def __googledoc(func, validator, returns=None, rtype=None):
    arg_fmt = "    {name} ({type}): {doc}"
    docstring_fmt = "{description}\n\n"
    if len(validator) > 0:
        docstring_fmt += "Args:\n{args}\n"
    ret_fmt = ("\nReturns:\n"
               "    {rtype}: {returns}")
    return __builddoc(func, validator, docstring_fmt, arg_fmt, ret_fmt=ret_fmt, returns=returns, rtype=rtype)


def getargs(*argnames):
    '''getargs(*argnames, argdict)
    Convenience function to retrieve arguments from a dictionary in batch
    '''
    if len(argnames) < 2:
        raise ValueError('Must supply at least one key and a dict')
    if not isinstance(argnames[-1], dict):
        raise ValueError('last argument must be dict')
    kwargs = argnames[-1]
    if len(argnames) == 2:
        return kwargs.get(argnames[0])
    return [kwargs.get(arg) for arg in argnames[:-1]]


def popargs(*argnames):
    '''popargs(*argnames, argdict)
    Convenience function to retrieve and remove arguments from a dictionary in batch
    '''
    if len(argnames) < 2:
        raise ValueError('Must supply at least one key and a dict')
    if not isinstance(argnames[-1], dict):
        raise ValueError('last argument must be dict')
    kwargs = argnames[-1]
    if len(argnames) == 2:
        return kwargs.pop(argnames[0])
    return [kwargs.pop(arg) for arg in argnames[:-1]]


class ExtenderMeta(ABCMeta):
    """A metaclass that will extend the base class initialization
       routine by executing additional functions defined in
       classes that use this metaclass

       In general, this class should only be used by core developers.
    """

    __preinit = '__preinit'

    @classmethod
    def pre_init(cls, func):
        setattr(func, cls.__preinit, True)
        return classmethod(func)

    __postinit = '__postinit'

    @classmethod
    def post_init(cls, func):
        '''A decorator for defining a routine to run after creation of a type object.

        An example use of this method would be to define a classmethod that gathers
        any defined methods or attributes after the base Python type construction (i.e. after
        :py:func:`type` has been called)
        '''
        setattr(func, cls.__postinit, True)
        return classmethod(func)

    def __init__(cls, name, bases, classdict):
        it = (getattr(cls, n) for n in dir(cls))
        it = (a for a in it if hasattr(a, cls.__preinit))
        for func in it:
            func(name, bases, classdict)
        super().__init__(name, bases, classdict)
        it = (getattr(cls, n) for n in dir(cls))
        it = (a for a in it if hasattr(a, cls.__postinit))
        for func in it:
            func(name, bases, classdict)


def get_data_shape(data, strict_no_data_load=False):
    """
    Helper function used to determine the shape of the given array.

    :param data: Array for which we should determine the shape.
    :type data: List, numpy.ndarray, DataChunkIterator, any object that support __len__ or .shape.
    :param strict_no_data_load: In order to determine the shape of nested tuples and lists, this function
                recursively inspects elements along the dimensions, assuming that the data has a regular,
                rectangular shape. In the case of out-of-core iterators this means that the first item
                along each dimensions would potentially be loaded into memory. By setting this option
                we enforce that this does not happen, at the cost that we may not be able to determine
                the shape of the array.
    :return: Tuple of ints indicating the size of known dimensions. Dimensions for which the size is unknown
             will be set to None.
    """
    def __get_shape_helper(local_data):
        shape = list()
        if hasattr(local_data, '__len__'):
            shape.append(len(local_data))
            if len(local_data) and not isinstance(local_data[0], (str, bytes)):
                shape.extend(__get_shape_helper(local_data[0]))
        return tuple(shape)
    if hasattr(data, 'maxshape'):
        return data.maxshape
    elif hasattr(data, 'shape'):
        return data.shape
    elif isinstance(data, dict):
        return None
    elif hasattr(data, '__len__') and not isinstance(data, (str, bytes)):
        if not strict_no_data_load or (isinstance(data, list) or isinstance(data, tuple) or isinstance(data, set)):
            return __get_shape_helper(data)
        else:
            return None
    else:
        return None


def pystr(s):
    """
    Convert a string of characters to Python str object
    """
    if isinstance(s, bytes):
        return s.decode('utf-8')
    else:
        return s


class LabelledDict(dict):
    """A dict wrapper class with a label and which allows retrieval of values based on an attribute of the values

    For example, if the key attribute is set as 'name' in __init__, then all objects added to the LabelledDict must have
    a 'name' attribute and a particular object in the LabelledDict can be accessed using the syntax ['object_name'] if
    the object.name == 'object_name'. In this way, LabelledDict acts like a set where values can be retrieved using
    square brackets around the value of the key attribute. An 'add' method makes clear the association between the key
    attribute of the LabelledDict and the values of the LabelledDict.

    LabelledDict also supports retrieval of values with the syntax my_dict['attr == val'], which returns a set of
    objects in the LabelledDict which have an attribute 'attr' with a string value 'val'. If no objects match that
    condition, a KeyError is raised. Note that if 'attr' equals the key attribute, then the single matching value is
    returned, not a set.

    Usage:
      LabelledDict(label='my_objects', def_key_name = 'name')
      my_dict[obj.name] = obj
      my_dict.add(obj)  # simpler syntax

    Example:
      # MyTestClass is a class with attributes 'prop1' and 'prop2'. MyTestClass.__init__ sets those attributes.
      ld = LabelledDict(label='all_objects', key_attr='prop1')
      obj1 = MyTestClass('a', 'b')
      obj2 = MyTestClass('d', 'b')
      ld[obj1.prop1] = obj1  # obj1 is added to the LabelledDict with the key obj1.prop1. Any other key is not allowed.
      ld.add(obj2)           # Simpler 'add' syntax enforces the required relationship
      ld['a']                # Returns obj1
      ld['prop1 == a']       # Also returns obj1
      ld['prop2 == b']       # Returns set([obj1, obj2]) - the set of all values v in ld where v.prop2 == 'b'
    """

    @docval({'name': 'label', 'type': str, 'doc': 'the label on this dictionary'},
            {'name': 'key_attr', 'type': str, 'doc': 'the attribute name to use as the key', 'default': 'name'})
    def __init__(self, **kwargs):
        label, key_attr = getargs('label', 'key_attr', kwargs)
        self.__label = label
        self.__key_attr = key_attr

    @property
    def label(self):
        """Return the label of this LabelledDict"""
        return self.__label

    @property
    def key_attr(self):
        """Return the attribute used as the key for values in this LabelledDict"""
        return self.__key_attr

    def __getitem__(self, args):
        """Get a value from the LabelledDict with the given key.

        Supports syntax my_dict['attr == val'], which returns a set of objects in the LabelledDict which have an
        attribute 'attr' with a string value 'val'. If no objects match that condition, a KeyError is raised.

        Note that if 'attr' equals the key attribute, then the single matching value is returned, not a set.
        """
        key = args
        if '==' in args:
            key, val = args.split("==")
            key = key.strip()
            val = val.strip()  # val is a string
            if not key:
                raise ValueError("An attribute name is required before '=='.")
            if not val:
                raise ValueError("A value is required after '=='.")
            if key != self.key_attr:
                ret = set()
                for item in self.values():
                    if getattr(item, key, None) == val:
                        ret.add(item)
                if len(ret):
                    return ret
                else:
                    raise KeyError(val)
            # if key == self.key_attr, then call __getitem__ normally on val
            key = val
        return super().__getitem__(key)

    def __setitem__(self, key, value):
        """Set a value in the LabelledDict with the given key. The key must equal value.key_attr.

        See LabelledDict.add for simpler syntax. Raises ValueError if value does not have attribute key_attr.
        """
        self.__check_value(value)
        if key != getattr(value, self.key_attr):
            raise KeyError("Key '%s' must equal attribute '%s' of '%s'." % (key, self.key_attr, value))
        super().__setitem__(key, value)

    def add(self, value):
        """Add a value to the dict with the key value.key_attr.

        Raises ValueError if value does not have attribute key_attr.
        """
        self.__check_value(value)
        self.__setitem__(getattr(value, self.key_attr), value)

    def __check_value(self, value):
        if not hasattr(value, self.key_attr):
            raise ValueError("Cannot set value '%s' in LabelledDict. Value must have key '%s'."
                             % (value, self.key_attr))
