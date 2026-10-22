package example;

public interface PaymentGateway {

    String charge(String orderId, long amountCents);
}
