"""Every way a log record can carry a value out of this process.

A no-leak claim about logs is a claim about the record rather than about
the line, because a handler this deployment does not configure is still
handed the whole object.

So `renderings` answers three readings of every record a case captured:
the JSON format, the text format, and the record itself, its whole
attribute dictionary, its unformatted arguments and whatever exception
it carries. A sentinel absent from all three is absent from a log file
whatever is configured in front of it.

The third reading is the one that earns its place, and what it adds is
narrower than "the formatters print only the message": this server's
JSON formatter serializes every non-standard `extra` and formats an
attached exception, so those two shapes are caught by the formatters
already. What it catches that neither of them does, measured rather
than assumed: an argument no placeholder consumed (a mapping passed
beside a message with no `%` in it formats away to the message alone),
and a value written onto one of logging's own attributes, which the
JSON formatter skips by name and the text format never prints. Both are
shapes a caller can reach by accident, and both leave the value in the
object a third-party handler serializes whole.

One home, because two suites make the same claim about the same value
from opposite sides of the wire: the API's write routes assert it about
a body they were sent, and the CLI's verbs assert it about a word an
operator typed. A walk copied into each of them is one decision in two
places, and the weaker of the two copies is the one nobody notices.

Every record and not this server's own alone, which is the whole of what
the claim can honestly mean: a credential in a client library's request
line is in the deployment's log file exactly as much as one in a line
this code wrote.

`config_cli.logged` is the other reading and stays what it is: one
string, per record, of the message and what the formatter would put back
into it, which is what the CLI suites that sweep a whole run assert
against. This is the stronger walk, for the cases whose subject is one
value and where it could have gone.
"""

import logging

import pytest

from vinga_server import logs


def renderings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every record captured, three ways: both formats a deployment
    writes one in, and the object behind them."""
    text = logging.Formatter(logs.TEXT_FORMAT)
    return [
        rendering
        for record in caplog.records
        for rendering in (
            logs.JsonFormatter().format(record),
            text.format(record),
            f"{record.getMessage()}\n{record.__dict__!r}\n{record.args!r}\n"
            f"{record.exc_info!r}\n{record.exc_text!r}",
        )
    ]
