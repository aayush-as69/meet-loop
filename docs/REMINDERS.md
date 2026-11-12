# Meetloop Reminders

## Events

Meetloop tracks reminder events around deadlines, including 24-hour, 1-hour and overdue conditions.

Each reminder event is intended to fire once for its corresponding condition.

## Weekly digest

The digest summarizes completed, due-soon and overdue work plus recent decisions. The UI includes an email preview; verify an actual outbound email integration separately before claiming live email delivery.

## Demo time

The reminders and digest views accept a simulated `now` value so deadline behavior can be demonstrated without waiting for real time.

Example:

~~~text
/reminders?now=2026-11-12T10:00
/digest?now=2026-11-12T10:00
~~~
