# -*- coding: utf-8 -*-

import itertools

from sqlparse import sql
from sqlparse import tokens as T
from sqlparse.utils import recurse, imt, find_matching


def _group_left_right(tlist, ttype, value, cls,
                      check_right=lambda t: True,
                      check_left=lambda t: True,
                      include_semicolon=False):
    [_group_left_right(sgroup, ttype, value, cls, check_right, check_left,
                       include_semicolon) for sgroup in tlist.get_sublists()
     if not isinstance(sgroup, cls)]
    idx = 0
    token = tlist.token_next_match(idx, ttype, value)
    while token:
        right = tlist.token_next(tlist.token_index(token))
        left = tlist.token_prev(tlist.token_index(token))
        if right is None or not check_right(right):
            token = tlist.token_next_match(tlist.token_index(token) + 1,
                                           ttype, value)
        elif left is None or not check_left(left):
            token = tlist.token_next_match(tlist.token_index(token) + 1,
                                           ttype, value)
        else:
            if include_semicolon:
                sright = tlist.token_next_match(tlist.token_index(right),
                                                T.Punctuation, ';')
                if sright is not None:
                    # only overwrite "right" if a semicolon is actually
                    # present.
                    right = sright
            tokens = tlist.tokens_between(left, right)[1:]
            if not isinstance(left, cls):
                new = cls([left])
                new_idx = tlist.token_index(left)
                tlist.tokens.remove(left)
                tlist.tokens.insert(new_idx, new)
                left = new
            left.tokens.extend(tokens)
            for t in tokens:
                tlist.tokens.remove(t)
            token = tlist.token_next_match(tlist.token_index(left) + 1,
                                           ttype, value)


def _group_matching(tlist, cls):
    """Groups Tokens that have beginning and end. ie. parenthesis, brackets.."""
    idx = 1 if imt(tlist, i=cls) else 0

    token = tlist.token_next_by(m=cls.M_OPEN, idx=idx)
    while token:
        end = find_matching(tlist, token, cls.M_OPEN, cls.M_CLOSE)
        if end is not None:
            token = tlist.group_tokens(cls, tlist.tokens_between(token, end))
            _group_matching(token, cls)
        token = tlist.token_next_by(m=cls.M_OPEN, idx=token)


def group_if(tlist):
    _group_matching(tlist, sql.If)


def group_for(tlist):
    _group_matching(tlist, sql.For)


def group_foreach(tlist):
    _group_matching(tlist, sql.For)


def group_begin(tlist):
    _group_matching(tlist, sql.Begin)


def group_as(tlist):
    def _right_valid(token):
        # Currently limited to DML/DDL. Maybe additional more non SQL reserved
        # keywords should appear here (see issue8).
        return token.ttype not in (T.DML, T.DDL)

    def _left_valid(token):
        if token.ttype is T.Keyword and token.value in ('NULL',):
            return True
        return token.ttype is not T.Keyword

    _group_left_right(tlist, T.Keyword, 'AS', sql.Identifier,
                      check_right=_right_valid,
                      check_left=_left_valid)


def group_assignment(tlist):
    _group_left_right(tlist, T.Assignment, ':=', sql.Assignment,
                      include_semicolon=True)


def group_comparison(tlist):
    def _parts_valid(token):
        return (token.ttype in (T.String.Symbol, T.String.Single,
                                T.Name, T.Number, T.Number.Float,
                                T.Number.Integer, T.Literal,
                                T.Literal.Number.Integer, T.Name.Placeholder)
                or isinstance(token, (sql.Identifier, sql.Parenthesis,
                                      sql.Function))
                or (token.ttype is T.Keyword
                    and token.value.upper() in ['NULL', ]))

    _group_left_right(tlist, T.Operator.Comparison, None, sql.Comparison,
                      check_left=_parts_valid, check_right=_parts_valid)


def group_case(tlist):
    _group_matching(tlist, sql.Case)


def group_identifier(tlist):
    def _consume_cycle(tl, i):
        # TODO: Usage of Wildcard token is ambivalent here.
        x = itertools.cycle((
            lambda y: (y.match(T.Punctuation, '.')
                       or y.ttype in (T.Operator,
                                      T.Wildcard,
                                      T.Name)
                       or isinstance(y, sql.SquareBrackets)),
            lambda y: (y.ttype in (T.String.Symbol,
                                   T.Name,
                                   T.Wildcard,
                                   T.Literal.String.Single,
                                   T.Literal.Number.Integer,
                                   T.Literal.Number.Float)
                       or isinstance(y, (sql.Parenthesis,
                                         sql.SquareBrackets,
                                         sql.Function)))))
        for t in tl.tokens[i:]:
            # Don't take whitespaces into account.
            if t.ttype is T.Whitespace:
                yield t
                continue
            if next(x)(t):
                yield t
            else:
                if isinstance(t, sql.Comment) and t.is_multiline():
                    yield t
                if t.ttype is T.Keyword.Order:
                    yield t
                return

    def _next_token(tl, i):
        # chooses the next token. if two tokens are found then the
        # first is returned.
        t1 = tl.token_next_by_type(
            i, (T.String.Symbol, T.Name, T.Literal.Number.Integer,
                T.Literal.Number.Float))

        i1 = tl.token_index(t1, start=i) if t1 else None
        t2_end = None if i1 is None else i1 + 1
        t2 = tl.token_next_by_instance(i, (sql.Function, sql.Parenthesis),
                                       end=t2_end)

        if t1 and t2:
            i2 = tl.token_index(t2, start=i)
            if i1 > i2:
                return t2
            else:
                return t1
        elif t1:
            return t1
        else:
            return t2

    # bottom up approach: group subgroups first
    [group_identifier(sgroup) for sgroup in tlist.get_sublists()
     if not isinstance(sgroup, sql.Identifier)]

    # real processing
    idx = 0
    token = _next_token(tlist, idx)
    while token:
        identifier_tokens = [token] + list(
            _consume_cycle(tlist,
                           tlist.token_index(token, start=idx) + 1))
        # remove trailing whitespace
        if identifier_tokens and identifier_tokens[-1].ttype is T.Whitespace:
            identifier_tokens = identifier_tokens[:-1]
        if not (len(identifier_tokens) == 1
                and (isinstance(identifier_tokens[0], (sql.Function,
                                                       sql.Parenthesis))
                     or identifier_tokens[0].ttype in (
                    T.Literal.Number.Integer, T.Literal.Number.Float))):
            group = tlist.group_tokens(sql.Identifier, identifier_tokens)
            idx = tlist.token_index(group, start=idx) + 1
        else:
            idx += 1
        token = _next_token(tlist, idx)


@recurse(sql.IdentifierList)
def group_identifier_list(tlist):
    # Allowed list items
    fend1_funcs = [lambda t: isinstance(t, (sql.Identifier, sql.Function,
                                            sql.Case)),
                   lambda t: t.is_whitespace(),
                   lambda t: t.ttype == T.Name,
                   lambda t: t.ttype == T.Wildcard,
                   lambda t: t.match(T.Keyword, 'null'),
                   lambda t: t.match(T.Keyword, 'role'),
                   lambda t: t.ttype == T.Number.Integer,
                   lambda t: t.ttype == T.String.Single,
                   lambda t: t.ttype == T.Name.Placeholder,
                   lambda t: t.ttype == T.Keyword,
                   lambda t: isinstance(t, sql.Comparison),
                   lambda t: isinstance(t, sql.Comment),
                   lambda t: t.ttype == T.Comment.Multiline,
                   ]
    tcomma = tlist.token_next_match(0, T.Punctuation, ',')
    start = None
    while tcomma is not None:
        # Go back one idx to make sure to find the correct tcomma
        idx = tlist.token_index(tcomma)
        before = tlist.token_prev(idx)
        after = tlist.token_next(idx)
        # Check if the tokens around tcomma belong to a list
        bpassed = apassed = False
        for func in fend1_funcs:
            if before is not None and func(before):
                bpassed = True
            if after is not None and func(after):
                apassed = True
        if not bpassed or not apassed:
            # Something's wrong here, skip ahead to next ","
            start = None
            tcomma = tlist.token_next_match(idx + 1,
                                            T.Punctuation, ',')
        else:
            if start is None:
                start = before
            after_idx = tlist.token_index(after, start=idx)
            next_ = tlist.token_next(after_idx)
            if next_ is None or not next_.match(T.Punctuation, ','):
                # Reached the end of the list
                tokens = tlist.tokens_between(start, after)
                group = tlist.group_tokens(sql.IdentifierList, tokens)
                start = None
                tcomma = tlist.token_next_match(tlist.token_index(group) + 1,
                                                T.Punctuation, ',')
            else:
                tcomma = next_


def group_brackets(tlist):
    _group_matching(tlist, sql.SquareBrackets)


def group_parenthesis(tlist):
    _group_matching(tlist, sql.Parenthesis)


@recurse(sql.Comment)
def group_comments(tlist):
    idx = 0
    token = tlist.token_next_by_type(idx, T.Comment)
    while token:
        tidx = tlist.token_index(token)
        end = tlist.token_not_matching(tidx + 1,
                                       [lambda t: t.ttype in T.Comment,
                                        lambda t: t.is_whitespace()])
        if end is None:
            idx = tidx + 1
        else:
            eidx = tlist.token_index(end)
            grp_tokens = tlist.tokens_between(token,
                                              tlist.token_prev(eidx, False))
            group = tlist.group_tokens(sql.Comment, grp_tokens)
            idx = tlist.token_index(group)
        token = tlist.token_next_by_type(idx, T.Comment)


@recurse(sql.Where)
def group_where(tlist):
    token = tlist.token_next_by(m=sql.Where.M_OPEN)
    while token:
        end = tlist.token_next_by(m=sql.Where.M_CLOSE, idx=token)

        if end is None:
            tokens = tlist.tokens_between(token, tlist._groupable_tokens[-1])
        else:
            tokens = tlist.tokens_between(
                token, tlist.tokens[tlist.token_index(end) - 1])

        token = tlist.group_tokens(sql.Where, tokens)
        token = tlist.token_next_by(m=sql.Where.M_OPEN, idx=token)


@recurse(sql.Identifier, sql.Function, sql.Case)
def group_aliased(tlist):
    clss = (sql.Identifier, sql.Function, sql.Case)
    idx = 0
    token = tlist.token_next_by_instance(idx, clss)
    while token:
        next_ = tlist.token_next(tlist.token_index(token))
        if next_ is not None and isinstance(next_, clss):
            if not next_.value.upper().startswith('VARCHAR'):
                grp = tlist.tokens_between(token, next_)[1:]
                token.tokens.extend(grp)
                for t in grp:
                    tlist.tokens.remove(t)
        idx = tlist.token_index(token) + 1
        token = tlist.token_next_by_instance(idx, clss)


def group_typecasts(tlist):
    _group_left_right(tlist, T.Punctuation, '::', sql.Identifier)


@recurse(sql.Function)
def group_functions(tlist):
    token = tlist.token_next_by(t=T.Name)
    while token:
        next_ = tlist.token_next(token)
        if imt(next_, i=sql.Parenthesis):
            tokens = tlist.tokens_between(token, next_)
            token = tlist.group_tokens(sql.Function, tokens)
        token = tlist.token_next_by(t=T.Name, idx=token)


def group_order(tlist):
    """Group together Identifier and Asc/Desc token"""
    token = tlist.token_next_by(t=T.Keyword.Order)
    while token:
        prev = tlist.token_prev(token)
        if imt(prev, i=sql.Identifier, t=T.Number):
            tokens = tlist.tokens_between(prev, token)
            token = tlist.group_tokens(sql.Identifier, tokens)
        token = tlist.token_next_by(t=T.Keyword.Order, idx=token)


@recurse()
def align_comments(tlist):
    token = tlist.token_next_by(i=sql.Comment)
    while token:
        before = tlist.token_prev(token)
        if isinstance(before, sql.TokenList):
            tokens = tlist.tokens_between(before, token)
            token = tlist.group_tokens(sql.TokenList, tokens, extend=True)
        token = tlist.token_next_by(i=sql.Comment, idx=token)


def group(tlist):
    for func in [
        group_comments,
        group_brackets,
        group_parenthesis,
        group_functions,
        group_where,
        group_case,
        group_identifier,
        group_order,
        group_typecasts,
        group_as,
        group_aliased,
        group_assignment,
        group_comparison,
        align_comments,
        group_identifier_list,
        group_if,
        group_for,
        group_foreach,
        group_begin,
    ]:
        func(tlist)
