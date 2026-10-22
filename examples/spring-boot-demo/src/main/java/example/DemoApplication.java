package example;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.metrics.buffering.BufferingApplicationStartup;

@SpringBootApplication
public class DemoApplication {

    public static void main(String[] args) {
        SpringApplication application = new SpringApplication(DemoApplication.class);
        // Required for GET /actuator/startup; never populated after the fact.
        application.setApplicationStartup(new BufferingApplicationStartup(2048));
        application.run(args);
    }
}
