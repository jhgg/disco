import six
import inspect
import functools

from holster.enum import BaseEnumMeta
from datetime import datetime as real_datetime

DATETIME_FORMATS = [
    '%Y-%m-%dT%H:%M:%S.%f',
    '%Y-%m-%dT%H:%M:%S'
]


class ConversionError(Exception):
    def __init__(self, field, raw, e):
        super(ConversionError, self).__init__(
            'Failed to convert `{}` (`{}`) to {}: {}'.format(
                raw, field.src_name, field.typ, e))


class FieldType(object):
    def __init__(self, typ):
        if isinstance(typ, FieldType) or inspect.isclass(typ) and issubclass(typ, Model):
            self.typ = typ
        elif isinstance(typ, BaseEnumMeta):
            self.typ = lambda raw, _: typ.get(raw)
        else:
            self.typ = lambda raw, _: typ(raw)

    def try_convert(self, raw, client):
        pass

    def __call__(self, raw, client):
        return self.try_convert(raw, client)


class Field(FieldType):
    def __init__(self, typ, alias=None):
        super(Field, self).__init__(typ)

        # Set names
        self.src_name = alias
        self.dst_name = None

        self.default = None

        if isinstance(self.typ, FieldType):
            self.default = self.typ.default

    def set_name(self, name):
        if not self.dst_name:
            self.dst_name = name

        if not self.src_name:
            self.src_name = name

    def has_default(self):
        return self.default is not None

    def try_convert(self, raw, client):
        try:
            return self.typ(raw, client)
        except Exception as e:
            raise ConversionError(self, raw, e)


class _Dict(FieldType):
    default = dict

    def __init__(self, typ, key=None):
        super(_Dict, self).__init__(typ)
        self.key = key

    def try_convert(self, raw, client):
        if self.key:
            converted = [self.typ(i, client) for i in raw]
            return {getattr(i, self.key): i for i in converted}
        else:
            return {k: self.typ(v, client) for k, v in six.iteritems(raw)}


class _List(FieldType):
    default = list

    def try_convert(self, raw, client):
        return [self.typ(i, client) for i in raw]


def _make(typ, data, client):
    if inspect.isclass(typ) and issubclass(typ, Model):
        return typ(data, client)
    return typ(data)


def snowflake(data):
    return int(data) if data else None


def enum(typ):
    def _f(data):
        return typ.get(data) if data else None
    return _f


def listof(*args, **kwargs):
    return _List(*args, **kwargs)


def dictof(*args, **kwargs):
    return _Dict(*args, **kwargs)


def datetime(data):
    if not data:
        return None

    for fmt in DATETIME_FORMATS:
        try:
            return real_datetime.strptime(data.rsplit('+', 1)[0], fmt)
        except (ValueError, TypeError):
            continue

    raise ValueError('Failed to conver `{}` to datetime'.format(data))


def text(obj):
    if six.PY2:
        return unicode(obj)
    else:
        return str(obj)


def binary(obj):
    if six.PY2:
        return unicode(obj)
    else:
        return bytes(obj)


def with_equality(field):
    class T(object):
        def __eq__(self, other):
            return getattr(self, field) == getattr(other, field)
    return T


def with_hash(field):
    class T(object):
        def __hash__(self, other):
            return hash(getattr(self, field))
    return T


class ModelMeta(type):
    def __new__(cls, name, parents, dct):
        fields = {}

        for k, v in six.iteritems(dct):
            if not isinstance(v, Field):
                continue

            v.set_name(k)
            fields[k] = v
            dct[k] = None

        dct['_fields'] = fields
        return super(ModelMeta, cls).__new__(cls, name, parents, dct)


class Model(six.with_metaclass(ModelMeta)):
    def __init__(self, *args, **kwargs):
        self.client = kwargs.pop('client', None)

        if len(args) == 1:
            obj = args[0]
        elif len(args) == 2:
            obj, self.client = args
        else:
            obj = kwargs

        for name, field in six.iteritems(self._fields):
            if field.src_name not in obj or not obj[field.src_name]:
                if field.has_default():
                    setattr(self, field.dst_name, field.default())
                continue

            value = field.try_convert(obj[field.src_name], self.client)
            setattr(self, field.dst_name, value)

    def update(self, other):
        for name in six.iterkeys(self._fields):
            value = getattr(other, name)
            if value:
                setattr(self, name, value)

        # Clear cached properties
        for name in dir(type(self)):
            if isinstance(getattr(type(self), name), property):
                try:
                    delattr(self, name)
                except:
                    pass

    def to_dict(self):
        return {k: getattr(self, k) for k in six.iterkeys(self._fields)}

    @classmethod
    def create(cls, client, data, **kwargs):
        inst = cls(data, client)
        inst.__dict__.update(kwargs)
        return inst

    @classmethod
    def create_map(cls, client, data):
        return list(map(functools.partial(cls.create, client), data))

    @classmethod
    def attach(cls, it, data):
        for item in it:
            for k, v in six.iteritems(data):
                try:
                    setattr(item, k, v)
                except:
                    # TODO: wtf
                    pass
