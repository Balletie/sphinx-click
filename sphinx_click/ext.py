import collections
import traceback

import click
from docutils import nodes, statemachine
from docutils.parsers import rst
from docutils.parsers.rst import directives

from sphinx.util.nodes import nested_parse_with_titles

def _indent(text, level=1):
    prefix = ' ' * (4 * level)

    def prefixed_lines():
        for line in text.splitlines(True):
            yield (prefix + line if line.strip() else line)

    return ''.join(prefixed_lines())


def _get_usage(ctx):
    """Alternative, non-prefixed version of 'get_usage'."""
    pieces = ctx.command.collect_usage_pieces(ctx)
    return '**' + ctx.command_path + '** ' + ' '.join(pieces)


def _get_help_record(opt):
    """Re-implementation of click.Opt.get_help_record.

    The variant of 'get_help_record' found in Click makes uses of slashes to
    separate multiple opts, and formats option arguments using upper case. This
    is not compatible with Sphinx's 'option' directive, which expects
    comma-separated opts and option arguments surrounded by angle brackets [1].

    [1] http://www.sphinx-doc.org/en/stable/domains.html#directive-option
    """

    def _write_opts(opts):
        rv, _ = click.formatting.join_options(opts)
        if not opt.is_flag and not opt.count:
            rv += ' {}'.format(opt.make_metavar())
        return rv

    rv = [_write_opts(opt.opts)]
    if opt.secondary_opts:
        rv.append(_write_opts(opt.secondary_opts))

    help = getattr(opt, 'rich_help', opt.help or '')
    extra = []
    if opt.default is not None and opt.show_default:
        extra.append('default: %s' %
                     (', '.join('%s' % d for d in opt.default)
                      if isinstance(opt.default,
                                    (list, tuple)) else opt.default, ))
    if opt.required:
        extra.append('required')
    if extra:
        help = '%s[%s]' % (help and help + '  ' or '', '; '.join(extra))

    return ', '.join(rv), help


def _format_description(ctx):
    """Format the description for a given `click.Command`.

    We parse this as reStructuredText, allowing users to embed rich
    information in their help messages if they so choose.
    """
    help_string = ctx.command.help or ctx.command.short_help
    if not help_string:
        return

    for line in statemachine.string2lines(
            help_string, tab_width=4, convert_whitespace=True):
        if line.strip() == '\b':
            continue
        yield line
    yield ''


def _format_usage(ctx):
    """Format the usage for a `click.Command`."""
    yield ''
    for line in _get_usage(ctx).splitlines():
        yield line
    yield ''


def _format_option(opt):
    """Format the output for a `click.Option`."""
    opt = _get_help_record(opt)

    yield '.. option:: {}'.format(opt[0])
    if opt[1]:
        yield ''
        for line in statemachine.string2lines(
                opt[1], tab_width=4, convert_whitespace=True):
            yield _indent(line)


def _format_options(ctx):
    """Format all `click.Option` for a `click.Command`."""
    # the hidden attribute is part of click 7.x only hence use of getattr
    params = [
        x for x in ctx.command.params
        if isinstance(x, click.Option) and not getattr(x, 'hidden', False)
    ]

    for param in params:
        for line in _format_option(param):
            yield line
        yield ''


def _format_argument(arg):
    """Format the output of a `click.Argument`."""
    yield '.. option:: {}'.format(arg.human_readable_name)
    yield ''
    yield _indent('{} argument{}'.format('Required'
                                         if arg.required else 'Optional', '(s)'
                                         if arg.nargs != 1 else ''))


def _format_arguments(ctx):
    """Format all `click.Argument` for a `click.Command`."""
    params = [x for x in ctx.command.params if isinstance(x, click.Argument)]

    for param in params:
        for line in _format_argument(param):
            yield line
        yield ''


def _format_envvar(param):
    """Format the envvars of a `click.Option` or `click.Argument`."""
    yield '.. envvar:: {}'.format(param.envvar)
    yield ''
    if isinstance(param, click.Argument):
        param_ref = param.human_readable_name
    else:
        # if a user has defined an opt with multiple "aliases", always use the
        # first. For example, if '--foo' or '-f' are possible, use '--foo'.
        param_ref = param.opts[0]

    yield _indent('Provide a default for :option:`{}`'.format(param_ref))


def _format_envvars(ctx):
    """Format all envvars for a `click.Command`."""
    params = [x for x in ctx.command.params if getattr(x, 'envvar')]

    for param in params:
        for line in _format_envvar(param):
            yield line
        yield ''


def _format_subcommand(command):
    """Format a sub-command of a `click.Command` or `click.Group`."""
    yield '.. object:: {}'.format(command.name)

    if command.short_help:
        yield ''
        for line in statemachine.string2lines(
                command.short_help, tab_width=4, convert_whitespace=True):
            yield _indent(line)


def _filter_commands(ctx, commands=None):
    """Return list of used commands."""
    if commands is None:
        return sorted(
            getattr(ctx.command, 'commands', {}).values(),
            key=lambda item: item.name)

    names = [name.strip() for name in commands.split(',')]
    lookup = getattr(ctx.command, 'commands', {})
    return [lookup[name] for name in names if name in lookup]


def _format_command(ctx, show_nested, commands=None):
    """Format the output of `click.Command`."""

    yield '.. program:: {}'.format(ctx.command_path)
    yield ''

    # usage
    yield 'Synopsis'
    yield '--------'
    yield ''

    for line in _format_usage(ctx):
        yield line

    # usage
    yield 'Description'
    yield '-----------'
    yield ''

    # description

    for line in _format_description(ctx):
        yield line

    # options

    lines = list(_format_options(ctx))
    if lines:
        # we use rubric to provide some separation without exploding the table
        # of contents
        yield 'Options'
        yield '-------'
        yield ''

    for line in lines:
        yield line

    # arguments

    lines = list(_format_arguments(ctx))
    if lines:
        yield 'Arguments'
        yield '---------'
        yield ''

    for line in lines:
        yield line

    # environment variables

    lines = list(_format_envvars(ctx))
    if lines:
        yield 'Environment variables'
        yield '---------------------'
        yield ''

    for line in lines:
        yield line

    # if we're nesting commands, we need to do this slightly differently
    if show_nested:
        return

    commands = _filter_commands(ctx, commands)

    if commands:
        yield 'Commands'
        yield '--------'
        yield ''

    for command in commands:
        for line in _format_subcommand(command):
            yield line
        yield ''


class AbstractClickDirective(rst.Directive):
    has_content = True
    required_arguments = 1
    option_spec = {
        'prog': directives.unchanged_required,
    }

    def _load_module(self, module_path):
        """Load the module."""

        # __import__ will fail on unicode,
        # so we ensure module path is a string here.
        module_path = str(module_path)

        try:
            module_name, attr_name = module_path.split(':', 1)
        except ValueError:  # noqa
            raise self.error(
                '"{}" is not of format "module:parser"'.format(module_path))

        try:
            mod = __import__(module_name, globals(), locals(), [attr_name])
        except (Exception, SystemExit) as exc:  # noqa
            err_msg = 'Failed to import "{}" from "{}". '.format(
                attr_name, module_name)
            if isinstance(exc, SystemExit):
                err_msg += 'The module appeared to call sys.exit()'
            else:
                err_msg += 'The following exception was raised:\n{}'.format(
                    traceback.format_exc())

            raise self.error(err_msg)

        if not hasattr(mod, attr_name):
            raise self.error('Module "{}" has no attribute "{}"'.format(
                module_name, attr_name))

        return getattr(mod, attr_name)

    def _get_context(self, name, command, parent=None):
        return click.Context(command, info_name=name, parent=parent)


class ClickDirective(AbstractClickDirective):
    has_content = False
    required_arguments = 1
    option_spec = {
        'prog': directives.unchanged_required,
        'show-nested': directives.flag,
        'auto': directives.flag,
        'commands': directives.unchanged,
    }

    def _generate_nodes(self,
                        name,
                        command,
                        parent=None,
                        show_nested=False,
                        commands=None):
        """Generate the relevant Sphinx nodes.

        Format a `click.Group` or `click.Command`.

        :param name: Name of command, as used on the command line
        :param command: Instance of `click.Group` or `click.Command`
        :param parent: Instance of `click.Context`, or None
        :param show_nested: Whether subcommands should be included in output
        :param commands: Display only listed commands or skip the section if
            empty
        :returns: A list of nested docutil nodes
        """
        node = nodes.Element()
        ctx = self._get_context(name, command, parent)

        # Summary

        source_name = ctx.command_path
        result = statemachine.ViewList()

        lines = _format_command(ctx, show_nested, commands)

        for line in lines:
            result.append(line, source_name)

        nested_parse_with_titles(self.state, result, node)
        
        # Subcommands
        if show_nested:
            commands = _filter_commands(ctx, commands)
            for command in commands:
                node.extend(
                    self._generate_nodes(command.name, command, ctx,
                                         show_nested))

        return node[:]

    def run(self):
        self.env = self.state.document.settings.env

        command = self._load_module(self.arguments[0])

        if 'prog' not in self.options:
            raise self.error(':prog: must be specified')

        prog_name = self.options.get('prog')
        show_nested = 'show-nested' in self.options
        commands = self.options.get('commands')

        self.env.temp_data = collections.ChainMap(
            {'click:command': command,
             'click:prog': prog_name,},
            self.env.temp_data
        )

        try:
            return self._generate_nodes(prog_name, command, None, show_nested,
                                        commands)
        finally:
            self.env.temp_data = self.env.temp_data.parents


class ClickOptionsDirective(AbstractClickDirective):
    has_content = False
    required_arguments = 0
    optional_arguments = 1
    option_spec = {
        'prog': directives.unchanged,
    }

    def _generate_nodes(self, prog_name, command):
        ctx = self._get_context(prog_name, command)

        lines = _format_options(ctx)
        node = nodes.Element()
        result = statemachine.ViewList()
        source_name = ctx.command_path

        for line in lines:
            result.append(line, source_name)

        nested_parse_with_titles(self.state, result, node)

        return node[:]

    def run(self):
        self.env = self.state.document.settings.env
        command = None
        if self.arguments:
            command = self._load_module(self.arguments[0])
        else:
            command = self.env.temp_data.get('click:command')

        prog_name = self.options.get(
            'prog',
            self.env.temp_data.get('click:prog', '')
        )

        if command is None:
            raise self.error(
                "The {} directive can only be used within a `click`"
                " directive.".format(self.name)
            )

        return self._generate_nodes(prog_name, command)


def setup(app):
    app.add_directive('click', ClickDirective)
    app.add_directive('click-options', ClickOptionsDirective)
