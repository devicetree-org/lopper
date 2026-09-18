#!/bin/bash
#
# Reject issue-tracker references that will not resolve for someone reading
# this repository.
#
# Contributions often arrive carrying a ticket id from whatever tracker the
# author's organisation uses. Those ids mean nothing to anyone outside it, and
# a commit message is permanent once merged, so they are worth catching before
# they land rather than after.
#
# Public, resolvable schemes (CVE, RFC, and similar) are allowed through.
#
# Usage:
#   scripts/check-references.sh --range <git-range>   # scan commit messages
#   scripts/check-references.sh --text <file>         # scan a file's contents
#   scripts/check-references.sh --string "some text"  # scan a literal string
#
# Any number of the above may be combined. Exits non-zero if anything matches.

set -u

# An identifier shaped like a tracker reference: a short uppercase prefix, a
# hyphen, then at least four digits.
PATTERN='\b[A-Z][A-Z0-9]{1,5}-[0-9]{4,}\b'

# Prefixes that are public and resolvable, so not a problem to reference.
ALLOWED='^(CVE|RFC|ISO|IEC|IEEE|PEP|ANSI|UL)$'

status=0

report() {
	# $1 = where it was found, $2 = the matching text
	printf '  %s: %s\n' "$1" "$2"
}

scan() {
	# $1 = label for messages, $2 = text to scan
	local label="$1" text="$2" hit prefix found=0

	while IFS= read -r hit; do
		[ -z "$hit" ] && continue
		prefix="${hit%%-*}"
		if printf '%s' "$prefix" | grep -qE "$ALLOWED"; then
			continue
		fi
		if [ "$found" -eq 0 ]; then
			found=1
			status=1
		fi
		report "$label" "$hit"
	done <<-EOF
		$(printf '%s' "$text" | grep -oE "$PATTERN" | sort -u)
	EOF
}

usage() {
	sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
	exit 2
}

[ $# -eq 0 ] && usage

while [ $# -gt 0 ]; do
	case "$1" in
	--range)
		[ $# -ge 2 ] || usage
		range="$2"
		shift 2
		# Scan each commit separately so the report names the offender.
		for sha in $(git rev-list "$range"); do
			scan "commit $(git log -1 --format=%h "$sha")" \
			     "$(git log -1 --format='%s%n%b' "$sha")"
		done
		;;
	--text)
		[ $# -ge 2 ] || usage
		[ -r "$2" ] || { printf 'cannot read %s\n' "$2" >&2; exit 2; }
		scan "$2" "$(cat "$2")"
		shift 2
		;;
	--string)
		[ $# -ge 2 ] || usage
		scan "text" "$2"
		shift 2
		;;
	-h | --help)
		usage
		;;
	*)
		printf 'unknown argument: %s\n' "$1" >&2
		usage
		;;
	esac
done

if [ "$status" -ne 0 ]; then
	cat >&2 <<-'EOF'

	Found references that will not resolve outside the organisation that
	created them. Please remove them.

	Commit messages are permanent once merged, so these have to be fixed by
	amending rather than by a follow-up commit. Pull request titles and
	descriptions can simply be edited.
	EOF
fi

exit "$status"
