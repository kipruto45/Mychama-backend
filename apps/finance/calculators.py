from decimal import Decimal, ROUND_HALF_UP


class LoanCalculator:
    @staticmethod
    def calculate_monthly_payment(principal, interest_rate, term_months):
        """
        Calculate monthly payment for a loan using the formula:
        M = P[r(1+r)^n]/[(1+r)^n-1]
        """
        if interest_rate == 0:
            return principal / term_months

        monthly_rate = interest_rate / 100 / 12
        numerator = principal * monthly_rate * (1 + monthly_rate) ** term_months
        denominator = (1 + monthly_rate) ** term_months - 1

        monthly_payment = numerator / denominator
        return monthly_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_total_amount_due(principal, interest_rate):
        """Calculate total amount due including interest"""
        return principal * (1 + interest_rate / 100)

    @staticmethod
    def calculate_remaining_balance(principal, monthly_payment, months_paid, interest_rate):
        """Calculate remaining balance after certain payments"""
        # This is a simplified calculation
        total_paid = monthly_payment * months_paid
        interest_paid = principal * (interest_rate / 100) * (months_paid / 12)
        return principal - (total_paid - interest_paid)


class ContributionCalculator:
    @staticmethod
    def calculate_total_contributions(contributions):
        """Calculate total contributions from a list of contribution objects"""
        return sum(contribution.amount for contribution in contributions)

    @staticmethod
    def calculate_average_contribution(contributions, period_months):
        """Calculate average monthly contribution"""
        total = ContributionCalculator.calculate_total_contributions(contributions)
        return total / period_months if period_months > 0 else 0


class PenaltyCalculator:
    @staticmethod
    def calculate_penalty_amount(base_amount, penalty_rate, days_overdue):
        """Calculate penalty amount based on rate and days overdue"""
        return base_amount * (penalty_rate / 100) * (days_overdue / 30)  # Assuming monthly rate