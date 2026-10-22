package example;

public class StripePaymentGateway implements PaymentGateway {

    private final StripeClient client;

    public StripePaymentGateway(StripeClient client) {
        this.client = client;
    }

    @Override
    public String charge(String orderId, long amountCents) {
        return client.charge(orderId, amountCents);
    }
}
