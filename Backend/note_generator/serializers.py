from rest_framework import serializers


class NoteSearchRequestSerializer(serializers.Serializer):
    query = serializers.CharField(min_length=1, max_length=2000, trim_whitespace=True)


class NoteSearchSourceSerializer(serializers.Serializer):
    note_id = serializers.IntegerField()
    title = serializers.CharField(allow_blank=True)
    source = serializers.CharField(allow_blank=True)


class NoteSearchResponseSerializer(serializers.Serializer):
    answer = serializers.CharField()
    sources = NoteSearchSourceSerializer(many=True)
