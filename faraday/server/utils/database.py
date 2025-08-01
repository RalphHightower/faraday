"""
Faraday Penetration Test IDE
Copyright (C) 2016  Infobyte LLC (https://faradaysec.com/)
See the file 'doc/LICENSE' for the license information
"""
# Standard library imports
import operator
from functools import reduce

from sqlalchemy import distinct, Boolean
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.ext import compiler
from sqlalchemy.sql import func, asc, desc
from sqlalchemy.sql.expression import ClauseElement, FunctionElement


class ORDER_DIRECTIONS:
    ASCENDING = 'asc'
    DESCENDING = 'desc'


def paginate(query, page, page_size):
    """
    Limit results from a query based on pagination parameters
    """
    if not (page >= 0 and page_size >= 0):
        raise ValueError(f"Invalid values for pagination (page: {page}, page_size: {page_size})")
    return query.limit(page_size).offset(page * page_size)


def sort_results(query, field_to_col_map, order_field, order_dir, default=None):
    """
    Apply sorting operations over a SQL query
    """
    order_cols = field_to_col_map.get(order_field, None)

    if order_cols and order_dir in (ORDER_DIRECTIONS.ASCENDING, ORDER_DIRECTIONS.DESCENDING):
        # Apply the proper sqlalchemy function for sorting direction over every
        # column declared on field_to_col_map[order_field]
        dir_func = asc if order_dir == ORDER_DIRECTIONS.ASCENDING else desc
        order_cols = list(map(dir_func, order_cols))
    else:
        # Use default ordering if declared if any parameter didn't met the requirements
        order_cols = [default] if default is not None else None

    return query.order_by(*order_cols) if order_cols else query


def apply_search_filter(query, field_to_col_map, free_text_search=None, field_filter={}, strict_filter=[]):
    """
    Build the filter for a SQL query from a free-text-search term or based on individual
    filters applied to labeled columns declared in field_to_col_map.

    FTS implementation is rudimentary since it applies the same LIKE filter for all
    declared columns in field_to_col_map, where the individual search terms stated
    in field_filter take precedence.
    """
    # Raise an error in case an asked column to filter by is not mapped
    if any(map(lambda attr: attr not in field_to_col_map, field_filter)):
        raise ValueError('Invalid field to filter')

    fts_sql_filter = None
    dfs_sql_filter = None

    # Iterate over every searchable field declared in the mapping
    # to then apply a filter on the query if required
    for attribute in field_to_col_map:
        is_direct_filter_search = attribute in field_filter
        is_free_text_search = not is_direct_filter_search and free_text_search

        # Add wildcards to both ends of a search term
        if is_direct_filter_search:
            like_str = '%' + field_filter.get(attribute) + '%'
        elif is_free_text_search:
            like_str = '%' + free_text_search + '%'
        else:
            continue

        search_term_sql_filter = None
        for column in field_to_col_map.get(attribute):
            # Labels are expressed as strings in the mapping,
            # currently we are not supporting searches on this
            # kind of fields since they are usually referred to
            # query built values (like counts)
            if isinstance(column, str):
                continue

            # Prepare a SQL search term according to the columns type.
            # As default we treat every column as an string and therefore
            # we use 'like' to search through them.
            if is_direct_filter_search and isinstance(column.type, Boolean):
                field_search_term = field_filter.get(attribute).lower()
                search_term = prepare_boolean_filter(column, field_search_term)
                # Ignore filter for this field if the values weren't expected
                if search_term is None:
                    continue
            else:
                # Strict filtering can be applied for fields. FTS will
                # ignore this list since its purpose is clearly to
                # match anything it can find.
                if is_direct_filter_search and attribute in strict_filter:
                    search_term = column.op('=')(field_filter.get(attribute))
                else:
                    search_term = column.like(like_str)

            search_term_sql_filter = concat_or_search_term(search_term_sql_filter, search_term)

        # Concatenate multiple search terms on its proper filter
        if is_direct_filter_search:
            dfs_sql_filter = concat_and_search_term(dfs_sql_filter, search_term_sql_filter)
        elif is_free_text_search:
            fts_sql_filter = concat_or_search_term(fts_sql_filter, search_term_sql_filter)

    sql_filter = concat_and_search_term(fts_sql_filter, dfs_sql_filter)
    return query.filter(sql_filter) if sql_filter is not None else query


def concat_and_search_term(left, right):
    return concat_search_terms(left, right, operator='and')


def concat_or_search_term(left, right):
    return concat_search_terms(left, right, operator='or')


def concat_search_terms(sql_filter_left, sql_filter_right, operator='and'):
    if sql_filter_left is None and sql_filter_right is None:
        return None
    elif sql_filter_left is None:
        return sql_filter_right
    elif sql_filter_right is None:
        return sql_filter_left
    else:
        if operator == 'and':
            return sql_filter_left & sql_filter_right
        elif operator == 'or':
            return sql_filter_left | sql_filter_right
        else:
            return None


def prepare_boolean_filter(column, search_term):
    if search_term in ['true', '1']:
        return column.is_(True)
    elif search_term in ['false', '0']:
        return column.is_(False) | column.is_(None)
    else:
        return None


def get_count(query, count_col=None):
    """
    Get a query row's count. This implementation performs significantly better
    than messaging a query's count method.
    """
    if count_col is None:
        count_filter = [func.count()]
    else:
        count_filter = [func.count(distinct(count_col))]

    count_q = query.statement.with_only_columns(count_filter). \
        order_by(None).group_by(None)
    count = query.session.execute(count_q).scalar()

    return count


def get_or_create(session, model, defaults=None, **kwargs):
    instance = session.query(model).filter_by(**kwargs).first()
    if instance:
        return instance, False
    else:
        params = {k: v for k, v in kwargs.items() if not isinstance(v, ClauseElement)}
        params.update(defaults or {})
        instance = model(**params)
        session.add(instance)
        return instance, True


class GroupConcat(FunctionElement):
    name = "group_concat"


@compiler.compiles(GroupConcat, 'postgresql')
def _group_concat_postgresql(element, compiler, **kw):
    if len(element.clauses) == 2:
        separator = compiler.process(element.clauses.clauses[1])
    else:
        separator = ','

    res = f'array_to_string(array_agg({compiler.process(element.clauses.clauses[0])}), \'{separator}\')'
    return res


class BooleanToIntColumn(FunctionElement):

    def __init__(self, expression):
        super().__init__()
        self.expression_str = expression


@compiler.compiles(BooleanToIntColumn, 'postgresql')
def _integer_to_boolean_postgresql(element, compiler, **kw):
    return f'{element.expression_str}::int'


@compiler.compiles(BooleanToIntColumn, 'sqlite')
def _integer_to_boolean_sqlite(element, compiler, **kw):
    return element.expression_str


def get_object_type_for(instance):
    object_type = instance.__tablename__
    if object_type is None:
        if instance.__class__.__name__ in ['Vulnerability',
                                           'VulnerabilityWeb',
                                           'VulnerabilityCode']:
            object_type = 'vulnerability'
        else:
            raise RuntimeError(f"Unknown table for object: {instance}")
    return object_type


def get_unique_fields(session, instance):
    table_name = get_object_type_for(instance)
    if table_name != 'vulnerability':
        engine = session.connection().engine
        insp = Inspector.from_engine(engine)
        unique_constraints = insp.get_unique_constraints(table_name)
    else:
        # Vulnerability unique index can't be retrieved via reflection.
        # If the unique index changes we need to update here.
        # A test should fail when the unique index changes
        unique_constraints = []
        unique_constraints.append({
            'column_names': [
                'name',
                'description',
                'type',
                'host_id',
                'service_id',
                'method',
                'parameter_name',
                'path',
                'website',
                'workspace_id',
            ]
        })
    if unique_constraints:
        for unique_constraint in unique_constraints:
            yield unique_constraint['column_names']


def get_conflict_object(session, obj, data, workspace=None, ids=None):
    unique_fields_gen = get_unique_fields(session, obj)
    for unique_fields in unique_fields_gen:
        relations_fields = list(filter(
            lambda unique_field: unique_field.endswith('_id'),
            unique_fields))
        unique_fields = list(filter(
            lambda unique_field: not unique_field.endswith('_id'),
            unique_fields))

        if get_object_type_for(obj) == 'vulnerability':
            # This is a special key due to model inheritance
            from faraday.server.models import VulnerabilityGeneric  # pylint:disable=import-outside-toplevel
            klass = VulnerabilityGeneric
        else:
            klass = obj.__class__

        table = klass.__table__
        assert (klass is not None and table is not None)

        filter_data = []
        for unique_field in unique_fields:
            column = table.columns[unique_field]
            try:
                value = data[unique_field]
            except KeyError:
                value = getattr(obj, unique_field)
                if not value and column.default:
                    value = column.default.arg
            if value is not None:
                filter_data.append(column == value)

        if 'workspace_id' in relations_fields:
            relations_fields.remove('workspace_id')
            if workspace:
                filter_data.append(table.columns['workspace_id'] == workspace.id)
            else:
                # if not workspace but there is a relationship it must be from context view
                workspaces_ids = session.query(klass.workspace_id).filter(klass.id.in_(ids)).subquery()
                filter_data.append(table.columns['workspace_id'].in_(workspaces_ids))
        for relations_field in relations_fields:
            if relations_field not in data and relations_field.strip('_id') in data:
                related_object = data[relations_field.strip('_id')]
                assert related_object.id is not None
                filter_data.append(
                    table.columns[relations_field] == related_object.id)
            else:
                relation_id = data.get(relations_field, None)
                if relation_id:
                    filter_data.append(
                        table.columns[relations_field] == relation_id)
        if filter_data:
            filter_data = reduce(operator.and_, filter_data)
            return session.query(klass).filter(filter_data).first()
        else:
            return


UNIQUE_VIOLATION = '23505'


def is_unique_constraint_violation(exception):
    from faraday.server.models import db  # pylint:disable=import-outside-toplevel
    if db.engine.dialect.name != 'postgresql':
        # Not implemented for RDMS other than postgres, we can live without
        # this since it is just an extra check
        return True
    assert isinstance(exception.orig.pgcode, str)
    return exception.orig.pgcode == UNIQUE_VIOLATION


NOT_NULL_VIOLATION = '23502'


def not_null_constraint_violation(exception):
    from faraday.server.models import db  # pylint:disable=import-outside-toplevel
    if db.engine.dialect.name != 'postgresql':
        # Not implemented for RDMS other than postgres, we can live without
        # this since it is just an extra check
        return True
    assert isinstance(exception.orig.pgcode, str)
    return exception.orig.pgcode == NOT_NULL_VIOLATION
