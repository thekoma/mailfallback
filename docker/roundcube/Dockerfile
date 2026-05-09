FROM roundcube/roundcubemail:latest

# Fix PHP 8.4 TypeError in compressMessageSet (string - string arithmetic)
RUN sed -i 's/\$incr = \$id - \$prev;/\$incr = (int)\$id - (int)\$prev;/' \
    /usr/src/roundcubemail/program/lib/Roundcube/rcube_imap_generic.php
