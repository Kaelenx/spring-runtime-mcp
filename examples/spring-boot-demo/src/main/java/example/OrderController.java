package example;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {

    @GetMapping("/orders/{id}")
    Order order(@PathVariable String id) {
        return new Order(id, "NEW");
    }

    public record Order(String id, String status) {
    }
}
