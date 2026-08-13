from app.text_normalization import TTSTextNormalizer


def test_phase1_normalization_examples() -> None:
    normalizer = TTSTextNormalizer()

    assert normalizer.normalize(
        "A recent SIM swap occurred on 2026-08-10T06:52:10.303110+00:00"
    ) == "A recent SIM swap occurred on August tenth, two thousand twenty-six at six fifty-two AM"

    assert normalizer.normalize(
        "roamingStatus: INTERNATIONAL_ROAMING, countryCode: 36, countryIsoCodes: ['HU']"
    ) == "roamingStatus: INTERNATIONAL_ROAMING, countryCode: 36, country: Hungary"

    assert normalizer.normalize(
        "An existing QoD session (sessionId: c479afe9-72d7-4585-b76f-93498c3b237f, qosStatus: REQUESTED)"
    ) == "An existing QoD session (sessionId: an identifier starting with c, 4, 7, and ending with 3, 7, F, qosStatus: REQUESTED)"


def test_decimal_coordinates_use_paced_digit_words() -> None:
    normalizer = TTSTextNormalizer()

    assert normalizer.normalize("latitude 24.7136, longitude 46.6753") == (
        "latitude twenty-four point seven, one, three, six, longitude forty-six point six, seven, five, three"
    )
