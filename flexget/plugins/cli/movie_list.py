from __future__ import unicode_literals, division, absolute_import
from builtins import *  # noqa pylint: disable=unused-import, redefined-builtin

from argparse import ArgumentParser, ArgumentTypeError

from sqlalchemy.orm.exc import NoResultFound

from flexget import options, plugin
from flexget.entry import Entry
from flexget.event import event
from flexget.manager import Session
from flexget.plugin import PluginError, DependencyError
from flexget.plugins.list.movie_list import get_list_by_exact_name, get_movie_lists, get_movies_by_list_id, \
    get_movie_by_title_and_year, MovieListMovie, get_db_movie_identifiers, MovieListList, MovieListBase, get_movie_by_id
from flexget.terminal import TerminalTable, TerminalTableError, table_parser, console
from flexget.utils.tools import split_title_year


def lookup_movie(title, session, identifiers=None):
    try:
        imdb_lookup = plugin.get_plugin_by_name('imdb_lookup').instance.lookup
    except DependencyError:
        imdb_lookup = None

    try:
        tmdb_lookup = plugin.get_plugin_by_name('tmdb_lookup').instance.lookup
    except DependencyError:
        tmdb_lookup = None

    if not (imdb_lookup or tmdb_lookup):
        return

    entry = Entry(title=title)
    if identifiers:
        for identifier in identifiers:
            for key, value in identifier.items():
                entry[key] = value
    try:
        imdb_lookup(entry, session=session)
    # TODO IMDB lookup raises PluginError instead of the normal ValueError
    except PluginError:
        tmdb_lookup(entry)

    # Return only if lookup was successful
    if entry.get('movie_name'):
        return entry
    return


def movie_list_keyword_type(identifier):
    if identifier.count('=') != 1:
        raise ArgumentTypeError('Received identifier in wrong format: {}, '
                                ' should be in keyword format like `imdb_id=tt1234567`'.format(identifier))
    name, value = identifier.split('=', 2)
    if name not in MovieListBase().supported_ids:
        raise ArgumentTypeError('Received unsupported identifier ID {}. Should be one of {}'
                                .format(identifier, ' ,'.join(MovieListBase().supported_ids)))
    return {name: value}


def do_cli(manager, options):
    """Handle movie-list subcommand"""
    if options.list_action == 'all':
        movie_list_lists(options)
        return

    if options.list_action == 'list':
        movie_list_list(options)
        return

    if options.list_action == 'add':
        movie_list_add(options)
        return

    if options.list_action == 'del':
        movie_list_del(options)
        return

    if options.list_action == 'purge':
        movie_list_purge(options)
        return


def movie_list_lists(options):
    """ Show all movie lists """
    lists = get_movie_lists()
    header = ['#', 'List Name']
    table_data = [header]
    for movie_list in lists:
        table_data.append([movie_list.id, movie_list.name])
    try:
        table = TerminalTable(options.table_type, table_data)
    except TerminalTableError as e:
        console('ERROR: {}'.format(e))
    else:
        console(table.output)


def movie_list_list(options):
    """List movie list"""
    with Session() as session:
        try:
            movie_list = get_list_by_exact_name(options.list_name)
        except NoResultFound:
            console('Could not find movie list with name {}'.format(options.list_name))
            return
    header = ['#', 'Movie Name', 'Movie year']
    header += MovieListBase().supported_ids
    table_data = [header]
    movies = get_movies_by_list_id(movie_list.id, order_by='added', descending=True, session=session)
    for movie in movies:
        movie_row = [movie.id, movie.title, movie.year or '']
        for identifier in MovieListBase().supported_ids:
            movie_row.append(movie.identifiers.get(identifier, ''))
        table_data.append(movie_row)
    title = '{} Movies in movie list: `{}`'.format(len(movies), options.list_name)
    try:
        table = TerminalTable(options.table_type, table_data, title, drop_columns=[5, 2, 4])
    except TerminalTableError as e:
        console('ERROR: {}'.format(e))
    else:
        console(table.output)


def movie_list_add(options):
    with Session() as session:
        try:
            movie_list = get_list_by_exact_name(options.list_name, session=session)
        except NoResultFound:
            console('Could not find movie list with name {}, creating'.format(options.list_name))
            movie_list = MovieListList(name=options.list_name)
            session.add(movie_list)
            session.commit()
        title, year = split_title_year(options.movie_title)
        console('Trying to lookup movie title: `{}`'.format(title))
        movie = lookup_movie(title=title, session=session, identifiers=options.identifiers)
        if not movie:
            console('ERROR: movie lookup failed for movie {}, aborting'.format(options.movie_title))
            return
        title = movie['movie_name']
        movie = get_movie_by_title_and_year(list_id=movie_list.id, title=title, year=year, session=session)
        if not movie:
            console("Adding movie with title {} to list {}".format(title, movie_list.name))
            movie = MovieListMovie(title=movie['movie_name'], year=year, list_id=movie_list.id)
        else:
            console("Movie with title {} already exist in list {}".format(title, movie_list.name))

        id_list = []
        if options.identifiers:
            id_list = options.identifiers
        else:
            for _id in MovieListBase().supported_ids:
                if movie.get(_id):
                    id_list.append({_id: movie.get(_id)})
        if id_list:
            console('Setting movie identifiers:')
            for ident in id_list:
                for key in ident:
                    console('{}: {}'.format(key, ident[key]))
            movie.ids = get_db_movie_identifiers(identifier_list=id_list, session=session)
        session.merge(movie)
        console('Successfully added movie {} to movie list {} '.format(title, movie_list.name))


def movie_list_del(options):
    with Session() as session:
        try:
            movie_list = get_list_by_exact_name(options.list_name)
        except NoResultFound:
            console('Could not find movie list with name {}'.format(options.list_name))
            return

        try:
            movie_exist = get_movie_by_id(list_id=movie_list.id, movie_id=int(options.movie), session=session)
        except NoResultFound:
            console('Could not find movie with ID {} in list `{}`'.format(int(options.movie), options.list_name))
            return
        except ValueError:
            title, year = split_title_year(options.movie_title)
            movie_exist = get_movie_by_title_and_year(list_id=movie_list.id, title=title, year=year, session=session)
        if not movie_exist:
            console('Could not find movie with title {} in list {}'.format(options.movie_title, options.list_name))
            return
        else:
            console('Removing movie {} from list {}'.format(movie_exist.title, options.list_name))
            session.delete(movie_exist)


def movie_list_purge(options):
    with Session() as session:
        try:
            movie_list = get_list_by_exact_name(options.list_name)
        except NoResultFound:
            console('Could not find movie list with name {}'.format(options.list_name))
            return
        console('Deleting list {}'.format(options.list_name))
        session.delete(movie_list)


@event('options.register')
def register_parser_arguments():
    # Common option to be used in multiple subparsers
    movie_parser = ArgumentParser(add_help=False)
    movie_parser.add_argument('movie_title', metavar='<MOVIE TITLE>', help="Title of the movie")

    name_or_id_parser = ArgumentParser(add_help=False)
    name_or_id_parser.add_argument('movie', metavar='<NAME or ID>', help="Title or ID of the movie")

    identifiers_parser = ArgumentParser(add_help=False)
    identifiers_parser.add_argument('-i', '--identifiers', metavar='<identifiers>', nargs='+',
                                    type=movie_list_keyword_type,
                                    help='Can be a string or a list of string with the format imdb_id=XXX,'
                                         ' tmdb_id=XXX, etc')
    list_name_parser = ArgumentParser(add_help=False)
    list_name_parser.add_argument('list_name', nargs='?', metavar='<LIST NAME>', default='movies',
                                  help='Name of movie list to operate on (Default is \'movies\')')
    # Register subcommand
    parser = options.register_command('movie-list', do_cli, help='View and manage movie lists')
    # Set up our subparsers
    subparsers = parser.add_subparsers(title='actions', metavar='<action>', dest='list_action')
    subparsers.add_parser('all', parents=[table_parser], help='Shows all existing movie lists')
    subparsers.add_parser('list', parents=[list_name_parser, table_parser], help='List movies from a list')
    subparsers.add_parser('add', parents=[list_name_parser, movie_parser, identifiers_parser],
                          help='Add a movie to a list')
    subparsers.add_parser('del', parents=[list_name_parser, name_or_id_parser],
                          help='Remove a movie from a list using its title or ID')
    subparsers.add_parser('purge', parents=[list_name_parser],
                          help='Removes an entire list with all of its movies. Use this with caution')
