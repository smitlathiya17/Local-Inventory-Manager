import pytest
from app.utils import (
    grams_to_kg_str, kg_str_to_grams,
    paise_to_rupees_str, rupee_str_to_paise,
    calc_line_total_paise, _indian_format, format_date,
)


class TestGramsKg:
    def test_zero(self):
        assert grams_to_kg_str(0) == '0.000'

    def test_whole(self):
        assert grams_to_kg_str(2000) == '2.000'

    def test_fractional(self):
        assert grams_to_kg_str(2500) == '2.500'

    def test_large(self):
        assert grams_to_kg_str(1240000) == '1240.000'

    def test_parse_int(self):
        assert kg_str_to_grams('2') == 2000

    def test_parse_decimal(self):
        assert kg_str_to_grams('2.5') == 2500

    def test_parse_three_dp(self):
        assert kg_str_to_grams('0.001') == 1

    def test_parse_with_comma(self):
        assert kg_str_to_grams('1,240') == 1240000

    def test_roundtrip(self):
        for g in (0, 1, 500, 2500, 99999):
            assert kg_str_to_grams(grams_to_kg_str(g)) == g

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            kg_str_to_grams('-1')

    def test_bad_input_raises(self):
        with pytest.raises(ValueError):
            kg_str_to_grams('abc')


class TestPaiseRupees:
    def test_zero(self):
        assert paise_to_rupees_str(0) == '₹0'

    def test_whole_rupees(self):
        assert paise_to_rupees_str(55000) == '₹550'

    def test_with_paise(self):
        assert paise_to_rupees_str(55050) == '₹550.50'

    def test_indian_grouping_large(self):
        assert paise_to_rupees_str(48250000) == '₹4,82,500'

    def test_parse_plain(self):
        assert rupee_str_to_paise('550') == 55000

    def test_parse_with_symbol(self):
        assert rupee_str_to_paise('₹550') == 55000

    def test_parse_decimal(self):
        assert rupee_str_to_paise('550.50') == 55050

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            rupee_str_to_paise('-10')


class TestCalcLineTotal:
    def test_exact(self):
        # ₹550/kg × 2 kg = ₹1100 → 110000 paise
        assert calc_line_total_paise(55000, 2000) == 110000

    def test_round_half_up(self):
        # 55000 paise/kg × 1500 g / 1000 = 82500 paise exactly
        assert calc_line_total_paise(55000, 1500) == 82500

    def test_fractional_rounds_half_up(self):
        # 3 paise/kg × 1 g / 1000 = 0.003 → rounds to 0
        assert calc_line_total_paise(3, 1) == 0

    def test_half_up_not_half_even(self):
        # 1 paise/kg × 500 g / 1000 = 0.5 → rounds UP to 1 (not banker's rounding)
        assert calc_line_total_paise(1, 500) == 1


class TestIndianFormat:
    def test_small(self):
        assert _indian_format(100) == '100'

    def test_thousands(self):
        assert _indian_format(1000) == '1,000'

    def test_lakhs(self):
        assert _indian_format(100000) == '1,00,000'

    def test_large(self):
        assert _indian_format(482500) == '4,82,500'

    def test_negative(self):
        assert _indian_format(-482500) == '-4,82,500'


class TestFormatDate:
    def test_basic(self):
        assert format_date('2026-10-26') == '26 Oct 2026'

    def test_single_digit_day(self):
        assert format_date('2026-01-05') == '5 Jan 2026'
