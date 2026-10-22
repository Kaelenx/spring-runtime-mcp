package example;

public class StripeClient {

    public String charge(String orderId, long amountCents) {
        return "stripe-" + orderId + "-" + amountCents;
    }
}
