package example;

import org.springframework.boot.autoconfigure.condition.ConditionalOnBean;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class StripeConfiguration {

    @Bean
    @ConditionalOnProperty(name = "stripe.enabled", havingValue = "true")
    StripeClient stripeClient() {
        return new StripeClient();
    }

    @Bean
    @ConditionalOnBean(StripeClient.class)
    PaymentGateway paymentGateway(StripeClient client) {
        return new StripePaymentGateway(client);
    }
}
